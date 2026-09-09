"""Fact-check gate. Phase 0 is human-only (D010): no evidence retrieval, no automated verdicts."""

from vme.factcheck.gate import HUMAN_RESOLUTIONS, FactCheckDecision, evaluate_claims

__all__ = ["HUMAN_RESOLUTIONS", "FactCheckDecision", "evaluate_claims"]
