from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import NOW, make_policy, make_source, requires_ffmpeg
from tests.fake_llm import FakeLlm
from tests.fake_stt import FakeSpeechToText
from vme.domain.models import BasisType, LlmValidationStatus, RankingTier
from vme.ingestion.register import register_local_media
from vme.ranking.features import PrefilterConfig
from vme.ranking.service import RankingConfig, RankingError, rank_transcript
from vme.ranking.weights import load_weights
from vme.rights.gate import RightsBlockedError
from vme.segmentation.segmenter import SegmentationConfig
from vme.segmentation.service import segment_and_store
from vme.storage.db import Store
from vme.transcription.service import transcribe_media

# 12 sentences ≈ 12 x ~2.9 s; with pauses the segmenter yields several candidates
SENTENCES = [
    f"Sentence number {i} talks about topic {i % 3} and makes a point about markets today."
    for i in range(12)
]


def _pipeline(
    store: Store, audio: Path, artifacts: Path, basis: BasisType = BasisType.OWNED
) -> str:
    store.sources.add(make_source())
    policy = make_policy(basis)
    store.policies.add(policy)
    store.sources.attach_policy("S001", policy.id)
    asset = register_local_media(store, "S001", audio, artifacts_dir=artifacts, now=NOW)
    transcript = transcribe_media(
        store, asset.id, FakeSpeechToText(SENTENCES, pause_ms=200), artifacts_dir=artifacts, now=NOW
    )
    segment_and_store(
        store, transcript.id, SegmentationConfig(min_ms=4000, target_ms=6000, max_ms=9000), now=NOW
    )
    return transcript.id


def _cfg(**kw: object) -> RankingConfig:
    base: dict[str, object] = {
        "finalists_k": 2,
        "prefilter": PrefilterConfig(min_ms=1000, max_ms=60000, min_words=5),
    }
    base.update(kw)
    return RankingConfig(**base)  # type: ignore[arg-type]


@requires_ffmpeg
def test_funnel_cheap_then_strong_on_finalists(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    tid = _pipeline(store, audio_wav, artifacts_dir)
    candidates = store.candidates.list(tid)
    assert len(candidates) > 2
    llm = FakeLlm()
    result = rank_transcript(store, tid, llm, load_weights(), _cfg(), now=NOW)

    assert result.prefiltered == 0
    assert result.cheap_scored == len(candidates) and result.strong_scored == 2
    assert [a for a, _ in llm.calls].count("cheap") == len(candidates)
    assert [a for a, _ in llm.calls].count("strong") == 2
    assert len(result.llm_calls) == len(candidates) + 2

    runs = result.runs
    assert [r.candidate_id for r in runs] == [
        r.candidate_id for r in store.ranking.list_runs(result.batch.id)
    ]
    assert runs == sorted(runs, key=lambda r: (-r.final_score, r.candidate_id))
    strong_runs = [r for r in runs if r.rationale.startswith("[strong]")]
    cheap_runs = [r for r in runs if r.rationale.startswith("[cheap]")]
    assert len(strong_runs) == 2 and len(cheap_runs) == len(candidates) - 2
    for r in runs:
        assert set(r.features) and "rights_risk" in r.risks and r.risks["rights_risk"] == 0.0
        assert r.llm_call_id is not None
        call = store.llm_calls.get(r.llm_call_id)
        assert call.purpose == "candidate_scoring" and call.prompt_name == "candidate_scorer"
        assert call.prompt_version == "1.0.0" and call.response is not None
        assert f"candidate:{r.candidate_id}" in call.input_artifact_refs
        assert call.validation_status is LlmValidationStatus.VALID
    batch = store.ranking.get_batch(result.batch.id)
    assert batch.weights_version.startswith("viral_v0") and batch.model_alias == "strong"
    assert store.ranking.list_batches(tid) == [batch]
    # a strong run's llm call is the strong-tier call
    assert store.llm_calls.get(strong_runs[0].llm_call_id or "").model_alias == "strong"


@requires_ffmpeg
def test_cheap_stage_skipped_when_few_candidates(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    tid = _pipeline(store, audio_wav, artifacts_dir)
    n = len(store.candidates.list(tid))
    llm = FakeLlm(aliases=("strong",))  # no cheap alias configured
    result = rank_transcript(store, tid, llm, load_weights(), _cfg(finalists_k=n), now=NOW)
    assert result.cheap_scored == 0 and result.strong_scored == n
    assert all(a == "strong" for a, _ in llm.calls)
    # but with more candidates than finalists the missing cheap alias is a visible error
    with pytest.raises(RankingError, match="cheap"):
        rank_transcript(store, tid, llm, load_weights(), _cfg(finalists_k=1), now=NOW)


@requires_ffmpeg
def test_prefilter_and_rights_risk_are_persisted(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    tid = _pipeline(store, audio_wav, artifacts_dir, basis=BasisType.TRANSFORMATIVE_REVIEW_REQUIRED)
    n = len(store.candidates.list(tid))
    llm = FakeLlm()
    cfg = _cfg(
        finalists_k=n, prefilter=PrefilterConfig(min_ms=1000, max_ms=60000, min_words=10_000)
    )
    result = rank_transcript(store, tid, llm, load_weights(), cfg, now=NOW)
    assert result.prefiltered == n and llm.calls == []
    for r in result.runs:
        assert r.final_score == 0.0 and r.llm_call_id is None
        assert r.rationale.startswith("[prefilter] prefilter: too_few_words")
        assert r.risks == {"rights_risk": 0.6}


@requires_ffmpeg
def test_failed_strong_call_is_recorded_and_cheap_score_kept(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    tid = _pipeline(store, audio_wav, artifacts_dir)
    llm = FakeLlm(fail_alias="strong")
    result = rank_transcript(store, tid, llm, load_weights(), _cfg(finalists_k=1), now=NOW)
    failed = [c for c in result.llm_calls if c.validation_status is LlmValidationStatus.FAILED]
    assert len(failed) == 1 and failed[0].error == "simulated failure"
    assert store.llm_calls.get(failed[0].id).error == "simulated failure"
    kept = [r for r in result.runs if "strong_failed_kept_cheap" in r.rationale]
    assert len(kept) == 1 and kept[0].final_score > 0


@requires_ffmpeg
def test_rank_is_gated_and_needs_candidates(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    tid = _pipeline(store, audio_wav, artifacts_dir)
    with pytest.raises(RankingError, match="no candidates"):
        rank_transcript(store, tid, FakeLlm(), load_weights(), _cfg(created_by="nobody"), now=NOW)
    unknown = make_policy(BasisType.UNKNOWN)
    store.policies.add(unknown)
    store.sources.attach_policy("S001", unknown.id)
    with pytest.raises(RightsBlockedError):
        rank_transcript(store, tid, FakeLlm(), load_weights(), _cfg(), now=NOW)
    assert store.ranking.list_batches(tid) == []


@requires_ffmpeg
def test_strong_finalists_rank_above_cheap_even_with_lower_scores(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    tid = _pipeline(store, audio_wav, artifacts_dir)
    n = len(store.candidates.list(tid))
    # strong tier scores LOWER than cheap: tiers are not on a comparable scale
    llm = FakeLlm(strong_boost=-0.3)
    result = rank_transcript(store, tid, llm, load_weights(), _cfg(finalists_k=2), now=NOW)
    tiers = [r.tier for r in result.runs]
    assert tiers[:2] == [RankingTier.STRONG, RankingTier.STRONG]
    assert all(t is RankingTier.CHEAP for t in tiers[2:]) and len(tiers) == n
    strong_scores = [r.final_score for r in result.runs[:2]]
    cheap_scores = [r.final_score for r in result.runs[2:]]
    assert max(strong_scores) < max(cheap_scores)  # the bug scenario from the first real run
    # persisted order matches: tier first, then score
    listed = store.ranking.list_runs(result.batch.id)
    assert [r.id for r in listed] == [r.id for r in result.runs]
    assert listed[0].tier is RankingTier.STRONG and listed[-1].tier is RankingTier.CHEAP
    assert listed[0].rationale.startswith("[strong]")
