from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from tests.conftest import NOW, make_policy, make_source, requires_ffmpeg
from tests.fake_llm import FakeLlm
from tests.fake_stt import FakeSpeechToText
from vme.domain.models import BasisType, Label, LabelDecision, PerformanceBucket
from vme.editorial.review import ReviewError, reject
from vme.editorial.service import generate_editorial
from vme.ingestion.register import register_local_media
from vme.labeling.service import LabelError, add_label, aggregate, golden_rows
from vme.labeling.taxonomy import UnknownReasonError, load_taxonomy, validate_reasons
from vme.ranking.features import PrefilterConfig
from vme.ranking.service import RankingConfig, rank_transcript
from vme.ranking.weights import load_weights
from vme.segmentation.segmenter import SegmentationConfig
from vme.segmentation.service import segment_and_store
from vme.storage.db import Store
from vme.transcription.service import transcribe_media

SENTENCES = [f"Sentence {i} shares a concrete market observation worth quoting." for i in range(12)]
TAX = load_taxonomy()


def _pipeline(store: Store, audio: Path, artifacts: Path) -> tuple[str, list[str]]:
    store.sources.add(make_source())
    policy = make_policy(BasisType.OWNED)
    store.policies.add(policy)
    store.sources.attach_policy("S001", policy.id)
    asset = register_local_media(store, "S001", audio, artifacts_dir=artifacts, now=NOW)
    t = transcribe_media(
        store, asset.id, FakeSpeechToText(SENTENCES, pause_ms=200), artifacts_dir=artifacts, now=NOW
    )
    cands = segment_and_store(
        store, t.id, SegmentationConfig(min_ms=4000, target_ms=6000, max_ms=9000), now=NOW
    )
    return t.id, [c.id for c in cands]


def test_taxonomy_loads_and_validates() -> None:
    assert TAX.version == "reasons_v1" and "weak_hook" in TAX.codes and "other" in TAX.codes
    assert validate_reasons([" Weak_Hook", "weak_hook", "too_long"], TAX, note=None) == [
        "weak_hook",
        "too_long",
    ]
    with pytest.raises(UnknownReasonError, match="unknown reason code"):
        validate_reasons(["meh"], TAX, note=None)
    with pytest.raises(UnknownReasonError, match="requires a note"):
        validate_reasons(["other"], TAX, note="")
    assert validate_reasons(["other"], TAX, note="explained") == ["other"]


@requires_ffmpeg
def test_add_label_rules(store: Store, audio_wav: Path, artifacts_dir: Path) -> None:
    _, cands = _pipeline(store, audio_wav, artifacts_dir)
    cid = cands[0]
    with pytest.raises(LabelError, match="reviewer"):
        add_label(store, cid, reviewer=" ", decision=LabelDecision.APPROVE, taxonomy=TAX, now=NOW)
    with pytest.raises(LabelError, match="at least one rejection reason"):
        add_label(store, cid, reviewer="a", decision=LabelDecision.REJECT, taxonomy=TAX, now=NOW)
    with pytest.raises(LabelError, match="cannot carry"):
        add_label(
            store,
            cid,
            reviewer="a",
            decision=LabelDecision.APPROVE,
            taxonomy=TAX,
            rejection_reasons=["weak_hook"],
            now=NOW,
        )
    with pytest.raises(UnknownReasonError):
        add_label(
            store,
            cid,
            reviewer="a",
            decision=LabelDecision.REJECT,
            taxonomy=TAX,
            rejection_reasons=["nope"],
            now=NOW,
        )
    lb = add_label(
        store,
        cid,
        reviewer="alice",
        decision=LabelDecision.APPROVE,
        taxonomy=TAX,
        boundary_correct=True,
        hook_quality=4,
        factual_risk=2,
        rights_risk=1,
        expected_performance=PerformanceBucket.HIGH,
        edited_text="  tighter cut  ",
        notes="good",
        now=NOW,
    )
    assert lb.taxonomy_version == "reasons_v1" and lb.edited_text == "tighter cut"
    assert store.labels.get(lb.id) == lb and store.labels.list(candidate_id=cid) == [lb]
    # append-only: a second label by the same reviewer is a new row, not an update
    lb2 = add_label(
        store,
        cid,
        reviewer="alice",
        decision=LabelDecision.REJECT,
        taxonomy=TAX,
        rejection_reasons=["too_long"],
        now=NOW + timedelta(minutes=1),
    )
    assert [x.id for x in store.labels.list(candidate_id=cid)] == [lb.id, lb2.id]
    with pytest.raises(Exception, match=r"range|less than"):
        Label(
            id="x",
            candidate_id=cid,
            reviewer="a",
            decision=LabelDecision.APPROVE,
            hook_quality=9,
            taxonomy_version="v",
        )


def _lbl(
    reviewer: str, decision: LabelDecision, bucket: PerformanceBucket | None = None, minute: int = 0
) -> Label:
    return Label(
        id=f"l_{reviewer}_{minute}",
        candidate_id="c",
        reviewer=reviewer,
        decision=decision,
        rejection_reasons=["weak_hook"] if decision is LabelDecision.REJECT else [],
        expected_performance=bucket,
        taxonomy_version="v",
        created_at=NOW + timedelta(minutes=minute),
    )


def test_aggregate_majority_ties_reject_and_latest_wins() -> None:
    d, rel, agr = aggregate([_lbl("a", LabelDecision.APPROVE, PerformanceBucket.HIGH)])
    assert d is LabelDecision.APPROVE and rel == 3.0 and agr is None
    d, rel, _ = aggregate([_lbl("a", LabelDecision.APPROVE)])
    assert rel == 2.0  # approved without a bucket
    d, rel, agr = aggregate([_lbl("a", LabelDecision.APPROVE), _lbl("b", LabelDecision.REJECT)])
    assert d is LabelDecision.REJECT and rel == 0.0 and agr == 0.5  # tie -> reject
    d, rel, agr = aggregate(
        [
            _lbl("a", LabelDecision.APPROVE, PerformanceBucket.LOW),
            _lbl("b", LabelDecision.APPROVE, PerformanceBucket.HIGH),
            _lbl("c", LabelDecision.REJECT),
        ]
    )
    assert d is LabelDecision.APPROVE and rel == 2.0 and agr == pytest.approx(2 / 3)
    # latest label per reviewer wins
    d, _, _ = aggregate(
        [_lbl("a", LabelDecision.APPROVE, minute=0), _lbl("a", LabelDecision.REJECT, minute=5)]
    )
    assert d is LabelDecision.REJECT


@requires_ffmpeg
def test_golden_rows_join_latest_ranking(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    tid, cands = _pipeline(store, audio_wav, artifacts_dir)
    add_label(
        store,
        cands[0],
        reviewer="a",
        decision=LabelDecision.APPROVE,
        taxonomy=TAX,
        expected_performance=PerformanceBucket.HIGH,
        now=NOW,
    )
    add_label(
        store,
        cands[1],
        reviewer="a",
        decision=LabelDecision.REJECT,
        taxonomy=TAX,
        rejection_reasons=["unclear"],
        now=NOW,
    )
    rows = golden_rows(store, tid)
    assert [r.candidate.id for r in rows] == [cands[0], cands[1]] and all(
        r.run is None for r in rows
    )
    result = rank_transcript(
        store,
        tid,
        FakeLlm(),
        load_weights(),
        RankingConfig(
            finalists_k=2, prefilter=PrefilterConfig(min_ms=1000, max_ms=60000, min_words=5)
        ),
        now=NOW,
    )
    rows = golden_rows(store, tid)
    assert all(r.run is not None and r.run.ranking_batch_id == result.batch.id for r in rows)
    assert rows[0].relevance == 3.0 and rows[1].relevance == 0.0
    payload = rows[0].to_json()
    assert payload["decision"] == "approve" and payload["ranking_run"]["candidate_id"] == cands[0]


@requires_ffmpeg
def test_review_reject_uses_taxonomy(store: Store, audio_wav: Path, artifacts_dir: Path) -> None:
    _, cands = _pipeline(store, audio_wav, artifacts_dir)
    draft = generate_editorial(
        store, cands[0], FakeLlm(editorial_claims=0, extractor_claims=0), now=NOW
    ).draft
    with pytest.raises(ReviewError, match="unknown reason code"):
        reject(store, draft.id, reviewer="bob", reason_codes=["meh"], now=NOW)
    out = reject(
        store, draft.id, reviewer="bob", reason_codes=["Weak_Hook", "other"], notes="x", now=NOW
    )
    assert out.event.reason_codes == ["weak_hook", "other"]


def test_provisional_labels_yield_to_human_labels() -> None:
    prov = _lbl("claude-bootstrap", LabelDecision.APPROVE, PerformanceBucket.HIGH)
    prov = prov.model_copy(update={"provisional": True})
    # alone: provisional labels drive the judgement
    d, rel, _ = aggregate([prov])
    assert d is LabelDecision.APPROVE and rel == 3.0
    # a human reject wins outright, it is not a tie with the provisional approve
    human = _lbl("alice", LabelDecision.REJECT, minute=1)
    d, rel, agr = aggregate([prov, human])
    assert d is LabelDecision.REJECT and rel == 0.0 and agr is None


@requires_ffmpeg
def test_provisional_share_is_reported(store: Store, audio_wav: Path, artifacts_dir: Path) -> None:
    tid, cands = _pipeline(store, audio_wav, artifacts_dir)
    add_label(
        store,
        cands[0],
        reviewer="bot",
        decision=LabelDecision.APPROVE,
        taxonomy=TAX,
        provisional=True,
        now=NOW,
    )
    add_label(
        store,
        cands[1],
        reviewer="bot",
        decision=LabelDecision.APPROVE,
        taxonomy=TAX,
        provisional=True,
        now=NOW,
    )
    add_label(
        store,
        cands[1],
        reviewer="alice",
        decision=LabelDecision.REJECT,
        taxonomy=TAX,
        rejection_reasons=["unclear"],
        now=NOW,
    )
    rows = golden_rows(store, tid)
    by_id = {r.candidate.id: r for r in rows}
    assert by_id[cands[0]].provisional is True and by_id[cands[1]].provisional is False
    assert by_id[cands[1]].decision is LabelDecision.REJECT
    assert store.labels.get(store.labels.list(candidate_id=cands[0])[0].id).provisional is True


@requires_ffmpeg
def test_import_labels_reports_bad_rows_and_keeps_good_ones(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    from vme.labeling.service import import_labels

    _, cands = _pipeline(store, audio_wav, artifacts_dir)
    records = [
        {
            "candidate_id": cands[0],
            "decision": "approve",
            "hook_quality": 4,
            "expected_performance": "HIGH",
        },
        {
            "candidate_id": cands[1],
            "decision": "reject",
            "rejection_reasons": "weak_hook, too_long",
        },
        {"candidate_id": cands[1], "decision": "reject", "rejection_reasons": ["nope"]},
        {"candidate_id": "ghost", "decision": "approve"},
        {"candidate_id": cands[2], "decision": "maybe"},
    ]
    res = import_labels(store, records, reviewer="bot", taxonomy=TAX, provisional=True, now=NOW)
    assert len(res.added) == 2 and all(lb.provisional and lb.reviewer == "bot" for lb in res.added)
    assert res.added[0].expected_performance is PerformanceBucket.HIGH
    assert res.added[1].rejection_reasons == ["weak_hook", "too_long"]
    assert [e["line"] for e in res.errors] == [3, 4, 5]
    assert (
        "UnknownReasonError" in res.errors[0]["error"] and "NotFoundError" in res.errors[1]["error"]
    )


@requires_ffmpeg
def test_candidate_key_round_trip_and_import_by_key(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    from vme.labeling.service import candidate_key, import_labels, resolve_candidate_key

    _, cands = _pipeline(store, audio_wav, artifacts_dir)
    c = store.candidates.get(cands[0])
    key = candidate_key(store, c)
    sha = store.media.list()[0].sha256
    assert key == f"{sha}:{c.start_ms}-{c.end_ms}"
    assert resolve_candidate_key(store, key) == c.id
    with pytest.raises(LabelError, match="no candidate matches"):
        resolve_candidate_key(store, f"{sha}:1-2")
    with pytest.raises(LabelError, match="malformed"):
        resolve_candidate_key(store, "garbage")
    res = import_labels(
        store, [{"candidate_key": key, "decision": "approve"}], reviewer="r", taxonomy=TAX, now=NOW
    )
    assert len(res.added) == 1 and res.added[0].candidate_id == c.id
    res = import_labels(
        store,
        [{"candidate_key": f"{sha}:1-2", "decision": "approve"}],
        reviewer="r",
        taxonomy=TAX,
        now=NOW,
    )
    assert res.added == [] and "no candidate matches" in res.errors[0]["error"]
