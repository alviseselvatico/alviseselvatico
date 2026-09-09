"""Human review of drafts and claims. Every decision is a ``ReviewEvent`` (audit log).

Approval re-checks the rights gate (``clip`` and ``render_transform``) and the fact-check
gate at decision time: a draft is never approved on a stale check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from vme.domain.models import (
    Claim,
    ClaimStatus,
    DraftStatus,
    EditorialVersion,
    InvalidTransitionError,
    ReviewEvent,
    check_transition,
    new_id,
    utc_now,
)
from vme.editorial.service import policy_for_candidate
from vme.factcheck.gate import HUMAN_RESOLUTIONS, evaluate_claims
from vme.logs import get_logger
from vme.rights.gate import Action, check
from vme.storage.db import Store

log = get_logger("review")

DRAFT = "editorial_version"
CLAIM = "claim"


class ReviewError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    draft: EditorialVersion
    event: ReviewEvent
    claims: list[Claim]


def _event(
    object_type: str,
    object_id: str,
    decision: str,
    reviewer: str,
    reasons: list[str],
    notes: str | None,
    now: datetime,
) -> ReviewEvent:
    if not reviewer.strip():
        msg = "reviewer identity is required (--reviewer or VME_REVIEWER)"
        raise ReviewError(msg)
    return ReviewEvent(
        id=new_id("rev"),
        object_type=object_type,
        object_id=object_id,
        decision=decision,
        reason_codes=reasons,
        notes=notes,
        reviewer=reviewer.strip(),
        created_at=now,
    )


def _move(
    store: Store, draft: EditorialVersion, new: DraftStatus, event: ReviewEvent
) -> EditorialVersion:
    check_transition(draft.status, new)
    with store.transaction():
        store.reviews.add(event)
        updated = store.editorial.set_status(draft.id, new)
    log.info(
        "draft_transition",
        extra={
            "editorial_version_id": draft.id,
            "from": draft.status.value,
            "to": new.value,
            "decision": event.decision,
            "reason_codes": event.reason_codes,
            "reviewer": event.reviewer,
        },
    )
    return updated


def _blocking_rights(store: Store, draft: EditorialVersion, now: datetime) -> list[str]:
    candidate = store.candidates.get(draft.candidate_id)
    policy = policy_for_candidate(store, candidate)
    codes: list[str] = []
    for action in (Action.CLIP, Action.RENDER_TRANSFORM):
        decision = check(policy, action, now=now)
        if decision.blocked:
            codes.append(f"rights_{decision.reason_code}")
    return codes


def approve(
    store: Store,
    draft_id: str,
    *,
    reviewer: str,
    notes: str | None = None,
    now: datetime | None = None,
) -> ReviewOutcome:
    """``needs_review -> approved`` unless rights or claims block it (then the block wins)."""
    now = now or utc_now()
    draft = store.editorial.get(draft_id)
    if draft.status is not DraftStatus.NEEDS_REVIEW:
        msg = f"draft {draft_id!r} is {draft.status.value}; only needs_review can be approved"
        raise ReviewError(msg)
    claims = store.claims.list(draft.id)
    fact = evaluate_claims(claims)
    if not fact.passes:
        event = _event(
            DRAFT, draft.id, "blocked_factcheck", reviewer, fact.reason_codes, notes, now
        )
        return ReviewOutcome(
            _move(store, draft, DraftStatus.BLOCKED_FACTCHECK, event), event, claims
        )
    rights = _blocking_rights(store, draft, now)
    if rights:
        event = _event(DRAFT, draft.id, "blocked_rights", reviewer, rights, notes, now)
        return ReviewOutcome(_move(store, draft, DraftStatus.BLOCKED_RIGHTS, event), event, claims)
    event = _event(DRAFT, draft.id, "approve", reviewer, [], notes, now)
    return ReviewOutcome(_move(store, draft, DraftStatus.APPROVED, event), event, claims)


def reject(
    store: Store,
    draft_id: str,
    *,
    reviewer: str,
    reason_codes: list[str],
    notes: str | None = None,
    now: datetime | None = None,
) -> ReviewOutcome:
    now = now or utc_now()
    reasons = [r.strip() for r in reason_codes if r.strip()]
    if not reasons:
        msg = "rejecting requires at least one reason code (labels are training data)"
        raise ReviewError(msg)
    draft = store.editorial.get(draft_id)
    if draft.status is not DraftStatus.NEEDS_REVIEW:
        msg = f"draft {draft_id!r} is {draft.status.value}; only needs_review can be rejected"
        raise ReviewError(msg)
    event = _event(DRAFT, draft.id, "reject", reviewer, reasons, notes, now)
    return ReviewOutcome(
        _move(store, draft, DraftStatus.REJECTED, event), event, store.claims.list(draft.id)
    )


def recheck(
    store: Store, draft_id: str, *, reviewer: str, now: datetime | None = None
) -> ReviewOutcome | None:
    """Move a blocked draft back to ``needs_review`` when its block no longer holds."""
    now = now or utc_now()
    draft = store.editorial.get(draft_id)
    claims = store.claims.list(draft.id)
    if draft.status is DraftStatus.BLOCKED_FACTCHECK and evaluate_claims(claims).passes:
        event = _event(DRAFT, draft.id, "factcheck_cleared", reviewer, [], None, now)
        return ReviewOutcome(_move(store, draft, DraftStatus.NEEDS_REVIEW, event), event, claims)
    if draft.status is DraftStatus.BLOCKED_RIGHTS and not _blocking_rights(store, draft, now):
        event = _event(DRAFT, draft.id, "rights_cleared", reviewer, [], None, now)
        return ReviewOutcome(_move(store, draft, DraftStatus.NEEDS_REVIEW, event), event, claims)
    return None


def resolve_claim(
    store: Store,
    claim_id: str,
    status: ClaimStatus,
    *,
    reviewer: str,
    notes: str | None = None,
    now: datetime | None = None,
) -> tuple[Claim, ReviewEvent, EditorialVersion]:
    """Operator resolution (D010): HUMAN_APPROVED or REMOVED only. Re-evaluates the draft."""
    now = now or utc_now()
    if status not in HUMAN_RESOLUTIONS:
        allowed = ", ".join(sorted(s.value for s in HUMAN_RESOLUTIONS))
        msg = f"Phase 0 fact check is human-only: status must be one of {allowed}"
        raise ReviewError(msg)
    claim = store.claims.get(claim_id)
    if not claim.blocking:
        msg = f"claim {claim_id!r} is already {claim.status.value}"
        raise ReviewError(msg)
    event = _event(CLAIM, claim.id, status.value.lower(), reviewer, [], notes, now)
    with store.transaction():
        store.reviews.add(event)
        claim = store.claims.set_status(claim.id, status, notes)
    log.info(
        "claim_resolved",
        extra={
            "claim_id": claim.id,
            "editorial_version_id": claim.editorial_version_id,
            "status": status.value,
            "reviewer": event.reviewer,
        },
    )
    draft = store.editorial.get(claim.editorial_version_id)
    try:
        outcome = recheck(store, draft.id, reviewer=reviewer, now=now)
    except InvalidTransitionError:  # pragma: no cover - defensive
        outcome = None
    return claim, event, outcome.draft if outcome else draft
