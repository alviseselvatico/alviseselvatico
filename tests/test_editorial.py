from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from tests.conftest import NOW, make_policy, make_source, requires_ffmpeg
from tests.fake_llm import FakeLlm
from tests.fake_stt import FakeSpeechToText
from vme.domain.models import (
    DRAFT_TRANSITIONS,
    BasisType,
    Claim,
    ClaimStatus,
    ClaimType,
    DraftStatus,
    InvalidTransitionError,
    check_transition,
)
from vme.editorial.review import ReviewError, approve, recheck, reject, resolve_claim
from vme.editorial.service import EditorialConfig, EditorialError, generate_editorial
from vme.factcheck.gate import evaluate_claims
from vme.ingestion.register import register_local_media
from vme.rights.gate import RightsBlockedError
from vme.segmentation.segmenter import SegmentationConfig
from vme.segmentation.service import segment_and_store
from vme.storage.db import Store
from vme.transcription.service import transcribe_media

SENTENCES = [f"Sentence {i} about markets with a personal view on revenue." for i in range(6)]


def _pipeline(
    store: Store, audio: Path, artifacts: Path, basis: BasisType = BasisType.OWNED
) -> str:
    store.sources.add(make_source())
    policy = make_policy(basis)
    store.policies.add(policy)
    store.sources.attach_policy("S001", policy.id)
    asset = register_local_media(store, "S001", audio, artifacts_dir=artifacts, now=NOW)
    t = transcribe_media(
        store, asset.id, FakeSpeechToText(SENTENCES, pause_ms=200), artifacts_dir=artifacts, now=NOW
    )
    cands = segment_and_store(
        store, t.id, SegmentationConfig(min_ms=4000, target_ms=8000, max_ms=12000), now=NOW
    )
    return cands[0].id


# ----------------------------------------------------------------- state machine


def test_transition_table_matches_data_model() -> None:
    assert DRAFT_TRANSITIONS[DraftStatus.NEEDS_REVIEW] == {
        DraftStatus.APPROVED,
        DraftStatus.REJECTED,
        DraftStatus.BLOCKED_RIGHTS,
        DraftStatus.BLOCKED_FACTCHECK,
    }
    check_transition(DraftStatus.BLOCKED_FACTCHECK, DraftStatus.NEEDS_REVIEW)
    check_transition(DraftStatus.APPROVED, DraftStatus.RETIRED)
    for bad in [
        (DraftStatus.GENERATED, DraftStatus.APPROVED),
        (DraftStatus.BLOCKED_FACTCHECK, DraftStatus.APPROVED),
        (DraftStatus.REJECTED, DraftStatus.NEEDS_REVIEW),
        (DraftStatus.NEEDS_REVIEW, DraftStatus.PUBLISHED),
    ]:
        with pytest.raises(InvalidTransitionError):
            check_transition(*bad)


def _claim(status: ClaimStatus) -> Claim:
    return Claim(
        id=f"c_{status.value}",
        editorial_version_id="e",
        claim_text="x",
        claim_type=ClaimType.FACT,
        status=status,
        origin="t",
        created_at=NOW,
    )


def test_factcheck_gate_blocking_statuses() -> None:
    assert evaluate_claims([]).passes
    ok = evaluate_claims(
        [
            _claim(ClaimStatus.HUMAN_APPROVED),
            _claim(ClaimStatus.REMOVED),
            _claim(ClaimStatus.SUPPORTED),
        ]
    )
    assert ok.passes and ok.blocking == []
    bad = evaluate_claims(
        [
            _claim(ClaimStatus.UNVERIFIED),
            _claim(ClaimStatus.CONTRADICTED),
            _claim(ClaimStatus.AMBIGUOUS),
        ]
    )
    assert not bad.passes and len(bad.blocking) == 3
    assert bad.reason_codes == ["claim_ambiguous", "claim_contradicted", "claim_unverified"]


# --------------------------------------------------------------------- generation


@requires_ffmpeg
def test_generate_creates_blocked_draft_with_unverified_claims(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    cid = _pipeline(store, audio_wav, artifacts_dir)
    llm = FakeLlm(editorial_claims=2, extractor_claims=2)
    result = generate_editorial(store, cid, llm, EditorialConfig(target_ms=30000), now=NOW)
    draft = result.draft
    assert draft.status is DraftStatus.BLOCKED_FACTCHECK and draft.version == 1
    assert draft.title == "The line everyone missed" and len(draft.title_options) == 2
    assert (
        draft.excerpt_plan and draft.excerpt_plan[0].start_ms >= store.candidates.get(cid).start_ms
    )
    assert draft.prompt_version == "1.0.0" and draft.model_alias == "strong"
    # union: 2 transformer claims + 2 extractor claims, one duplicate by text -> 3
    assert len(result.claims) == 3
    assert all(c.status is ClaimStatus.UNVERIFIED for c in result.claims)
    origins = {c.origin for c in result.claims}
    assert origins == {"editorial_transformer:1.0.0", "claim_extractor:1.0.0"}
    assert store.claims.list(draft.id) == result.claims
    assert [a for a, _ in llm.calls] == ["strong", "cheap"]
    purposes = [store.llm_calls.get(c.id).purpose for c in result.llm_calls]
    assert purposes == ["editorial_transform", "claim_extraction"]
    assert store.editorial.list(cid) == [draft]
    # second generation is version 2
    assert generate_editorial(store, cid, llm, now=NOW).draft.version == 2


@requires_ffmpeg
def test_generate_without_claims_goes_to_needs_review(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    cid = _pipeline(store, audio_wav, artifacts_dir)
    result = generate_editorial(
        store, cid, FakeLlm(editorial_claims=0, extractor_claims=0), now=NOW
    )
    assert result.draft.status is DraftStatus.NEEDS_REVIEW and result.claims == []


@requires_ffmpeg
def test_generate_uses_strong_for_claims_when_no_cheap(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    cid = _pipeline(store, audio_wav, artifacts_dir)
    llm = FakeLlm(aliases=("strong",))
    generate_editorial(store, cid, llm, now=NOW)
    assert [a for a, _ in llm.calls] == ["strong", "strong"]


@requires_ffmpeg
def test_generate_is_gated_on_render_transform(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    cid = _pipeline(store, audio_wav, artifacts_dir)
    p = make_policy(BasisType.EXPLICIT_LICENSE, id="pol_noxf", can_transform=False)
    store.policies.add(p)
    store.sources.attach_policy("S001", p.id)
    with pytest.raises(RightsBlockedError) as excinfo:
        generate_editorial(store, cid, FakeLlm(), now=NOW)
    assert excinfo.value.decision.reason_code == "render_transform_not_permitted"
    assert store.editorial.list(cid) == []


@requires_ffmpeg
def test_generate_failure_records_call_and_raises(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    cid = _pipeline(store, audio_wav, artifacts_dir)
    with pytest.raises(EditorialError, match="editorial generation failed"):
        generate_editorial(store, cid, FakeLlm(fail_prompt="editorial_transformer"), now=NOW)
    assert store.editorial.list(cid) == []
    assert [c.validation_status.value for c in store.llm_calls.list()] == ["FAILED"]
    with pytest.raises(EditorialError, match="claim extraction failed"):
        generate_editorial(store, cid, FakeLlm(fail_prompt="claim_extractor"), now=NOW)
    assert store.editorial.list(cid) == []


# ------------------------------------------------------------------------- review


@requires_ffmpeg
def test_review_flow_resolve_claims_then_approve(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    cid = _pipeline(store, audio_wav, artifacts_dir)
    result = generate_editorial(
        store, cid, FakeLlm(editorial_claims=1, extractor_claims=2), now=NOW
    )
    draft = result.draft
    assert draft.status is DraftStatus.BLOCKED_FACTCHECK
    with pytest.raises(ReviewError, match="only needs_review"):
        approve(store, draft.id, reviewer="alice", now=NOW)
    # Phase 0: only human resolutions
    with pytest.raises(ReviewError, match="human-only"):
        resolve_claim(store, result.claims[0].id, ClaimStatus.SUPPORTED, reviewer="alice", now=NOW)
    with pytest.raises(ReviewError, match="reviewer identity"):
        resolve_claim(
            store, result.claims[0].id, ClaimStatus.HUMAN_APPROVED, reviewer="  ", now=NOW
        )

    claims = result.claims
    assert len(claims) == 2  # transformer claim (0) duplicates extractor claim (0)
    _, ev, d = resolve_claim(
        store, claims[0].id, ClaimStatus.HUMAN_APPROVED, reviewer="alice", notes="checked", now=NOW
    )
    assert d.status is DraftStatus.BLOCKED_FACTCHECK and ev.object_type == "claim"
    _, _, d = resolve_claim(store, claims[1].id, ClaimStatus.REMOVED, reviewer="alice", now=NOW)
    assert d.status is DraftStatus.NEEDS_REVIEW  # cleared automatically
    with pytest.raises(ReviewError, match="already"):
        resolve_claim(store, claims[1].id, ClaimStatus.REMOVED, reviewer="alice", now=NOW)

    outcome = approve(store, draft.id, reviewer="alice", notes="ship it", now=NOW)
    assert outcome.draft.status is DraftStatus.APPROVED and outcome.event.decision == "approve"
    events = store.reviews.list("editorial_version", draft.id)
    assert [e.decision for e in events] == ["factcheck_cleared", "approve"]
    assert all(e.reviewer == "alice" for e in events)
    assert len(store.reviews.list("claim", claims[0].id)) == 1


@requires_ffmpeg
def test_reject_requires_reason_and_is_terminal(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    cid = _pipeline(store, audio_wav, artifacts_dir)
    draft = generate_editorial(
        store, cid, FakeLlm(editorial_claims=0, extractor_claims=0), now=NOW
    ).draft
    with pytest.raises(ReviewError, match="reason code"):
        reject(store, draft.id, reviewer="bob", reason_codes=[" "], now=NOW)
    outcome = reject(
        store, draft.id, reviewer="bob", reason_codes=["weak_hook", "boundary_wrong"], now=NOW
    )
    assert outcome.draft.status is DraftStatus.REJECTED
    assert outcome.event.reason_codes == ["weak_hook", "boundary_wrong"]
    with pytest.raises(ReviewError):
        approve(store, draft.id, reviewer="bob", now=NOW)


@requires_ffmpeg
def test_approve_rechecks_rights_at_decision_time(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    cid = _pipeline(store, audio_wav, artifacts_dir)
    draft = generate_editorial(
        store, cid, FakeLlm(editorial_claims=0, extractor_claims=0), now=NOW
    ).draft
    # policy expires between generation and approval
    expired = make_policy(BasisType.OWNED, id="pol_exp", expiry_at=NOW + timedelta(hours=1))
    store.policies.add(expired)
    store.sources.attach_policy("S001", "pol_exp")
    outcome = approve(store, draft.id, reviewer="alice", now=NOW + timedelta(days=1))
    assert outcome.draft.status is DraftStatus.BLOCKED_RIGHTS
    assert outcome.event.decision == "blocked_rights"
    assert outcome.event.reason_codes == ["rights_policy_expired", "rights_policy_expired"]
    assert recheck(store, draft.id, reviewer="alice", now=NOW + timedelta(days=1)) is None
    # rights restored -> recheck lifts the block -> approve works
    fresh = make_policy(BasisType.OWNED, id="pol_fresh")
    store.policies.add(fresh)
    store.sources.attach_policy("S001", "pol_fresh")
    lifted = recheck(store, draft.id, reviewer="alice", now=NOW + timedelta(days=1))
    assert lifted is not None and lifted.draft.status is DraftStatus.NEEDS_REVIEW
    assert (
        approve(store, draft.id, reviewer="alice", now=NOW + timedelta(days=1)).draft.status
        is DraftStatus.APPROVED
    )


@requires_ffmpeg
def test_approve_rechecks_claims_added_later(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    cid = _pipeline(store, audio_wav, artifacts_dir)
    draft = generate_editorial(
        store, cid, FakeLlm(editorial_claims=0, extractor_claims=0), now=NOW
    ).draft
    store.claims.add_many(
        [
            Claim(
                id="clm_late",
                editorial_version_id=draft.id,
                claim_text="late",
                claim_type=ClaimType.FACT,
                origin="test",
                created_at=NOW,
            )
        ]
    )
    outcome = approve(store, draft.id, reviewer="alice", now=NOW)
    assert outcome.draft.status is DraftStatus.BLOCKED_FACTCHECK and outcome.event.reason_codes == [
        "claim_unverified"
    ]
