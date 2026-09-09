"""Fail-closed source policy gate (docs/RIGHTS_AND_PLATFORM_GUARDRAILS.md §5).

The gate checks capability flags only. The flags are trustworthy because
:class:`vme.domain.RightsPolicy` enforces D009 at construction time. The single
time-dependent rule — an expired policy evaluates as ``BLOCKED`` — is applied here
because it depends on *now*.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from vme.domain.models import RightsPolicy, utc_now


class Action(StrEnum):
    INGEST = "ingest"
    CLIP = "clip"
    RENDER_TRANSFORM = "render_transform"
    PUBLISH = "publish"


#: Flag that must be true for each action, in addition to ``can_ingest`` which is
#: always required (nothing happens to a source that may not be ingested).
_ACTION_FLAG: dict[Action, str] = {
    Action.INGEST: "can_ingest",
    Action.CLIP: "can_extract_clip",
    Action.RENDER_TRANSFORM: "can_transform",
    Action.PUBLISH: "can_publish",
}


@dataclass(frozen=True, slots=True)
class GateDecision:
    allowed: bool
    action: Action
    policy_id: str | None
    reason_code: str
    detail: str
    review_required: bool = False

    @property
    def blocked(self) -> bool:
        return not self.allowed


class RightsBlockedError(Exception):
    """Raised by :func:`require` when the gate blocks an action."""

    def __init__(self, decision: GateDecision) -> None:
        self.decision = decision
        super().__init__(f"{decision.reason_code}: {decision.detail}")


def check(
    policy: RightsPolicy | None,
    action: Action,
    *,
    now: datetime | None = None,
) -> GateDecision:
    """Evaluate whether ``action`` is permitted under ``policy``. Never raises."""
    now = now or utc_now()
    if policy is None:
        return GateDecision(
            allowed=False,
            action=action,
            policy_id=None,
            reason_code="no_policy",
            detail="source has no rights policy; unknown rights status defaults to BLOCK",
        )
    if policy.is_expired(now):
        expiry = policy.expiry_at.isoformat() if policy.expiry_at else "?"
        return GateDecision(
            allowed=False,
            action=action,
            policy_id=policy.id,
            reason_code="policy_expired",
            detail=f"policy expired at {expiry}; evaluates as BLOCKED regardless of flags",
        )
    if not policy.can_ingest:
        return GateDecision(
            allowed=False,
            action=action,
            policy_id=policy.id,
            reason_code="ingest_not_permitted",
            detail=f"can_ingest=false (basis_type={policy.basis_type.value})",
        )
    flag = _ACTION_FLAG[action]
    if not getattr(policy, flag):
        return GateDecision(
            allowed=False,
            action=action,
            policy_id=policy.id,
            reason_code=f"{action.value}_not_permitted",
            detail=f"{flag}=false (basis_type={policy.basis_type.value})",
        )
    return GateDecision(
        allowed=True,
        action=action,
        policy_id=policy.id,
        reason_code="allowed",
        detail=f"{flag}=true (basis_type={policy.basis_type.value})",
        review_required=policy.review_required,
    )


def require(
    policy: RightsPolicy | None,
    action: Action,
    *,
    now: datetime | None = None,
) -> GateDecision:
    """Like :func:`check` but raises :class:`RightsBlockedError` when blocked."""
    decision = check(policy, action, now=now)
    if decision.blocked:
        raise RightsBlockedError(decision)
    return decision
