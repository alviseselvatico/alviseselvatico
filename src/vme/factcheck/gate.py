"""Claim gate: any blocking claim keeps the draft in ``blocked_factcheck``."""

from __future__ import annotations

from dataclasses import dataclass

from vme.domain.models import Claim, ClaimStatus

#: The only statuses an operator may assign in Phase 0 (D010). SUPPORTED/CONTRADICTED/
#: AMBIGUOUS belong to the Phase 1 evidence evaluator.
HUMAN_RESOLUTIONS: frozenset[ClaimStatus] = frozenset(
    {ClaimStatus.HUMAN_APPROVED, ClaimStatus.REMOVED}
)


@dataclass(frozen=True, slots=True)
class FactCheckDecision:
    passes: bool
    blocking: list[Claim]

    @property
    def reason_codes(self) -> list[str]:
        return sorted({f"claim_{c.status.value.lower()}" for c in self.blocking})


def evaluate_claims(claims: list[Claim]) -> FactCheckDecision:
    blocking = [c for c in claims if c.blocking]
    return FactCheckDecision(passes=not blocking, blocking=blocking)
