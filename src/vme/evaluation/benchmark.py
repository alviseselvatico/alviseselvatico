"""Persist a benchmark of one ranking batch against human labels and compare two of them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from vme.domain.models import TIER_RANK, Benchmark, LabelDecision, new_id, utc_now
from vme.evaluation.metrics import (
    calibration_by_bucket,
    ndcg_at_k,
    pairwise_agreement,
    precision_at_k,
)
from vme.labeling.service import golden_rows
from vme.logs import get_logger
from vme.storage.db import Store

log = get_logger("evaluation")

MIN_LABELED_FOR_EXIT = 50  # ROADMAP Phase 1 exit criterion
DEFAULT_KS = (3, 5, 10)


class BenchmarkError(RuntimeError):
    pass


def run_benchmark(
    store: Store, batch_id: str, *, ks: tuple[int, ...] = DEFAULT_KS, now: datetime | None = None
) -> Benchmark:
    """Evaluate the batch's ranking (tier first, then score) against aggregated labels."""
    now = now or utc_now()
    batch = store.ranking.get_batch(batch_id)
    rows = [r for r in golden_rows(store, batch.transcript_id, batch_id) if r.run is not None]
    if not rows:
        msg = f"batch {batch_id!r} has no labeled candidates; add labels first"
        raise BenchmarkError(msg)
    # System order: the batch's own ordering restricted to labeled candidates.
    rows.sort(key=lambda r: (TIER_RANK[r.run.tier], -r.run.final_score, r.candidate.id))  # type: ignore[union-attr]
    relevant = [r.decision is LabelDecision.APPROVE for r in rows]
    relevance = [r.relevance for r in rows]
    # Scores comparable only within a tier; encode tier so pairwise respects the funnel order.
    scores = [(2 - TIER_RANK[r.run.tier]) * 1000 + r.run.final_score for r in rows]  # type: ignore[union-attr]
    metrics: dict[str, Any] = {
        "n_labeled": len(rows),
        "n_approved": sum(relevant),
        "approval_rate": sum(relevant) / len(rows),
        "pairwise_agreement": pairwise_agreement(scores, relevance),
        "calibration": calibration_by_bucket(
            [r.run.final_score for r in rows],  # type: ignore[union-attr]
            relevant,
        ),
        "reviewer_agreement": _mean(
            [r.reviewer_agreement for r in rows if r.reviewer_agreement is not None]
        ),
        "n_provisional": sum(r.provisional for r in rows),
        "provisional_share": sum(r.provisional for r in rows) / len(rows),
        "meets_exit_size": sum(not r.provisional for r in rows) >= MIN_LABELED_FOR_EXIT,
    }
    for k in ks:
        metrics[f"precision_at_{k}"] = precision_at_k(relevant, k)
        metrics[f"ndcg_at_{k}"] = ndcg_at_k(relevance, k)
    bench = Benchmark(
        id=new_id("bch"),
        ranking_batch_id=batch.id,
        transcript_id=batch.transcript_id,
        scoring_version=batch.scoring_version,
        weights_version=batch.weights_version,
        prompt_version=batch.prompt_version,
        model_alias=batch.model_alias,
        n_labeled=len(rows),
        metrics=metrics,
        created_at=now,
    )
    with store.transaction():
        store.benchmarks.add(bench)
    log.info(
        "benchmark_recorded",
        extra={
            "benchmark_id": bench.id,
            "batch_id": batch.id,
            "n_labeled": bench.n_labeled,
            "pairwise_agreement": metrics["pairwise_agreement"],
            "precision_at_5": metrics.get("precision_at_5"),
            "versions": [bench.scoring_version, bench.weights_version, bench.prompt_version],
        },
    )
    return bench


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


@dataclass(frozen=True, slots=True)
class Comparison:
    baseline_id: str
    candidate_id: str
    same_versions: bool
    deltas: dict[str, float | None]
    regressions: list[str]

    def to_json(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline_id,
            "candidate": self.candidate_id,
            "same_versions": self.same_versions,
            "deltas": self.deltas,
            "regressions": self.regressions,
            "regressed": bool(self.regressions),
        }


def compare_benchmarks(
    store: Store, baseline_id: str, candidate_id: str, *, tolerance: float = 0.02
) -> Comparison:
    """Metric deltas (candidate minus baseline); a drop beyond ``tolerance`` is a regression."""
    base = store.benchmarks.get(baseline_id)
    cand = store.benchmarks.get(candidate_id)
    keys = [
        k
        for k in cand.metrics
        if k.startswith(("precision_at_", "ndcg_at_")) or k == "pairwise_agreement"
    ]
    deltas: dict[str, float | None] = {}
    regressions: list[str] = []
    for k in sorted(keys):
        b, c = base.metrics.get(k), cand.metrics.get(k)
        if isinstance(b, int | float) and isinstance(c, int | float):
            delta = round(float(c) - float(b), 4)
            deltas[k] = delta
            if delta < -tolerance:
                regressions.append(f"{k}: {b:.3f} -> {c:.3f}")
        else:
            deltas[k] = None
    same = (base.scoring_version, base.weights_version, base.prompt_version) == (
        cand.scoring_version,
        cand.weights_version,
        cand.prompt_version,
    )
    return Comparison(baseline_id, candidate_id, same, deltas, regressions)
