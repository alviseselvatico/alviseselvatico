"""Fact-check gate (D010) and the Phase 1 automated evaluator (D030).

The evaluator lives in ``vme.factcheck.evaluator`` and is imported by path: it depends on
``vme.editorial.review``, which depends on the gate here, so importing it eagerly would
create a cycle.
"""

from vme.factcheck.gate import HUMAN_RESOLUTIONS, FactCheckDecision, evaluate_claims

__all__ = ["HUMAN_RESOLUTIONS", "FactCheckDecision", "evaluate_claims"]
