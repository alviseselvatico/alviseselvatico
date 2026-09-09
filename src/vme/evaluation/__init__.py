"""Offline ranking evaluation (DATA_MODEL §5): metrics, persisted benchmarks, comparison."""

from vme.evaluation.benchmark import BenchmarkError, compare_benchmarks, run_benchmark
from vme.evaluation.metrics import (
    calibration_by_bucket,
    ndcg_at_k,
    pairwise_agreement,
    precision_at_k,
)

__all__ = [
    "BenchmarkError",
    "calibration_by_bucket",
    "compare_benchmarks",
    "ndcg_at_k",
    "pairwise_agreement",
    "precision_at_k",
    "run_benchmark",
]
