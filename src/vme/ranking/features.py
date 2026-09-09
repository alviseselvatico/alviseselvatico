"""Deterministic stage of the funnel: rights risk and the cheap prefilter."""

from __future__ import annotations

from dataclasses import dataclass

from vme.domain.models import BasisType, Candidate, RightsPolicy

#: Rights risk derived from the policy (never asked to the LLM). Blocked/unknown never
#: reach ranking (gate), so 1.0 is only a defensive default.
_RIGHTS_RISK: dict[BasisType, float] = {
    BasisType.OWNED: 0.0,
    BasisType.EXPLICIT_LICENSE: 0.0,
    BasisType.CREATOR_AUTHORIZATION: 0.0,
    BasisType.PUBLIC_DOMAIN_VERIFIED: 0.1,
    BasisType.TRANSFORMATIVE_REVIEW_REQUIRED: 0.6,
}


def rights_risk(policy: RightsPolicy) -> float:
    return _RIGHTS_RISK.get(policy.basis_type, 1.0)


@dataclass(frozen=True, slots=True)
class PrefilterConfig:
    min_ms: int = 8_000
    max_ms: int = 90_000
    min_words: int = 15


def prefilter_reason(candidate: Candidate, config: PrefilterConfig) -> str | None:
    """Return why a candidate is dropped before any LLM call, or ``None`` to keep it.

    These are editorial feasibility limits for a vertical clip, not rights rules
    (guardrails §3: no duration rule is a copyright safe harbour).
    """
    if candidate.duration_ms < config.min_ms:
        return f"too_short: {candidate.duration_ms} ms < {config.min_ms} ms"
    if candidate.duration_ms > config.max_ms:
        return f"too_long: {candidate.duration_ms} ms > {config.max_ms} ms"
    words = len(candidate.candidate_text.split())
    if words < config.min_words:
        return f"too_few_words: {words} < {config.min_words}"
    return None
