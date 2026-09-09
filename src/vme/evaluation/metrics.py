"""Pure ranking metrics. Inputs are already ordered by the system's ranking (best first)."""

from __future__ import annotations

import math
from itertools import combinations


def precision_at_k(relevant: list[bool], k: int) -> float | None:
    """Share of the top ``k`` that humans approved. ``None`` when nothing to rank."""
    if k < 1 or not relevant:
        return None
    top = relevant[:k]
    return sum(top) / len(top)


def ndcg_at_k(relevance: list[float], k: int) -> float | None:
    """Graded NDCG. ``None`` when no positive relevance exists (ideal DCG is zero)."""
    if k < 1 or not relevance:
        return None
    top = relevance[:k]
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(top))
    ideal = sorted(relevance, reverse=True)[:k]
    idcg = sum(r / math.log2(i + 2) for i, r in enumerate(ideal))
    return dcg / idcg if idcg > 0 else None


def pairwise_agreement(scores: list[float], relevance: list[float]) -> float | None:
    """Among pairs humans ordered strictly, share the system ordered the same way.

    Pairs the system ties count as half agreement; pairs humans tie are skipped.
    """
    pairs = 0
    agree = 0.0
    for i, j in combinations(range(len(scores)), 2):
        if relevance[i] == relevance[j]:
            continue
        pairs += 1
        human = relevance[i] > relevance[j]
        if scores[i] == scores[j]:
            agree += 0.5
        elif (scores[i] > scores[j]) == human:
            agree += 1
    return agree / pairs if pairs else None


def calibration_by_bucket(
    scores: list[float], relevant: list[bool], buckets: int = 5
) -> list[dict[str, float | int]]:
    """Approval rate per score bucket (equal-width over [0, 100]). Empty buckets are kept."""
    width = 100.0 / buckets
    out: list[dict[str, float | int]] = []
    for b in range(buckets):
        lo, hi = b * width, (b + 1) * width
        idx = [i for i, s in enumerate(scores) if lo <= s < hi or (b == buckets - 1 and s == 100.0)]
        n = len(idx)
        rate = sum(relevant[i] for i in idx) / n if n else 0.0
        out.append({"bucket": b, "score_from": lo, "score_to": hi, "n": n, "approval_rate": rate})
    return out
