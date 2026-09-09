"""Viral Score v0 aggregation (DATA_MODEL §2). Pure and deterministic."""

from __future__ import annotations

from dataclasses import dataclass

from vme.ranking.weights import Weights

SCORING_VERSION = "viral_score_v0.1.0"


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    raw: float
    penalty: float
    final: float


def compute_score(
    components: dict[str, float], penalties: dict[str, float], weights: Weights
) -> ScoreBreakdown:
    """``final = clamp(sum(w_i*c_i) - sum(p_j*q_j), 0, 100)`` with every input in [0, 1]."""
    for name, value in (*components.items(), *penalties.items()):
        if not 0.0 <= value <= 1.0:
            msg = f"{name}={value} is outside [0, 1]"
            raise ValueError(msg)
    missing = set(weights.components) - set(components)
    if missing:
        msg = f"missing components: {sorted(missing)}"
        raise ValueError(msg)
    missing = set(weights.penalties) - set(penalties)
    if missing:
        msg = f"missing penalties: {sorted(missing)}"
        raise ValueError(msg)
    raw = sum(weights.components[k] * components[k] for k in weights.components)
    penalty = sum(weights.penalties[k] * penalties[k] for k in weights.penalties)
    final = min(100.0, max(0.0, raw - penalty))
    return ScoreBreakdown(raw=round(raw, 4), penalty=round(penalty, 4), final=round(final, 4))
