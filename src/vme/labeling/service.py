"""Append-only candidate labels and the golden-set view over them (D027)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from vme.domain.models import (
    Candidate,
    Label,
    LabelDecision,
    PerformanceBucket,
    RankingRun,
    new_id,
    utc_now,
)
from vme.labeling.taxonomy import ReasonTaxonomy, validate_reasons
from vme.logs import get_logger
from vme.storage.db import Store

log = get_logger("labeling")

_BUCKET_GRADE: dict[PerformanceBucket, float] = {
    PerformanceBucket.LOW: 1.0,
    PerformanceBucket.MEDIUM: 2.0,
    PerformanceBucket.HIGH: 3.0,
}


class LabelError(RuntimeError):
    pass


def add_label(
    store: Store,
    candidate_id: str,
    *,
    reviewer: str,
    decision: LabelDecision,
    taxonomy: ReasonTaxonomy,
    boundary_correct: bool | None = None,
    hook_quality: int | None = None,
    factual_risk: int | None = None,
    rights_risk: int | None = None,
    rejection_reasons: list[str] | None = None,
    expected_performance: PerformanceBucket | None = None,
    edited_text: str | None = None,
    notes: str | None = None,
    provisional: bool = False,
    now: datetime | None = None,
) -> Label:
    now = now or utc_now()
    if not reviewer.strip():
        msg = "reviewer identity is required (--reviewer or VME_REVIEWER)"
        raise LabelError(msg)
    store.candidates.get(candidate_id)
    reasons = validate_reasons(rejection_reasons or [], taxonomy, note=notes)
    if decision is LabelDecision.REJECT and not reasons:
        msg = "a reject label needs at least one rejection reason from the taxonomy"
        raise LabelError(msg)
    if decision is LabelDecision.APPROVE and reasons:
        msg = "an approve label cannot carry rejection reasons"
        raise LabelError(msg)
    label = Label(
        id=new_id("lbl"),
        candidate_id=candidate_id,
        reviewer=reviewer.strip(),
        decision=decision,
        boundary_correct=boundary_correct,
        hook_quality=hook_quality,
        factual_risk=factual_risk,
        rights_risk=rights_risk,
        rejection_reasons=reasons,
        expected_performance=expected_performance,
        edited_text=(edited_text or "").strip() or None,
        notes=notes,
        taxonomy_version=taxonomy.version,
        provisional=provisional,
        created_at=now,
    )
    with store.transaction():
        store.labels.add(label)
    log.info(
        "label_added",
        extra={
            "label_id": label.id,
            "candidate_id": candidate_id,
            "reviewer": label.reviewer,
            "decision": decision.value,
            "reasons": reasons,
            "expected_performance": expected_performance.value if expected_performance else None,
            "provisional": provisional,
        },
    )
    return label


@dataclass(frozen=True, slots=True)
class GoldenRow:
    """One candidate with its aggregated human judgement (and the latest ranking, if any)."""

    candidate: Candidate
    labels: list[Label]  # latest per reviewer
    decision: LabelDecision
    relevance: float  # 0 = reject; 1..3 = expected bucket (2 when approved without bucket)
    reviewer_agreement: float | None  # share of reviewers agreeing with the majority
    run: RankingRun | None
    provisional: bool = False  # True when only provisional labels back the judgement

    def to_json(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.model_dump(mode="json"),
            "decision": self.decision.value,
            "relevance": self.relevance,
            "reviewer_agreement": self.reviewer_agreement,
            "provisional": self.provisional,
            "labels": [lb.model_dump(mode="json") for lb in self.labels],
            "ranking_run": self.run.model_dump(mode="json") if self.run else None,
        }


def latest_per_reviewer(labels: list[Label]) -> list[Label]:
    latest: dict[str, Label] = {}
    for lb in sorted(labels, key=lambda x: x.created_at):
        latest[lb.reviewer] = lb
    return list(latest.values())


def effective_labels(labels: list[Label]) -> list[Label]:
    """Latest per reviewer; provisional labels count only while no human label exists (D028)."""
    latest = latest_per_reviewer(labels)
    human = [lb for lb in latest if not lb.provisional]
    return human or latest


def aggregate(labels: list[Label]) -> tuple[LabelDecision, float, float | None]:
    """Majority decision (ties reject), graded relevance, agreement share."""
    latest = effective_labels(labels)
    approvals = [lb for lb in latest if lb.decision is LabelDecision.APPROVE]
    approve = len(approvals) * 2 > len(latest)
    decision = LabelDecision.APPROVE if approve else LabelDecision.REJECT
    agreeing = len(approvals) if approve else len(latest) - len(approvals)
    agreement = agreeing / len(latest) if len(latest) > 1 else None
    if not approve:
        return decision, 0.0, agreement
    grades = [_BUCKET_GRADE[lb.expected_performance] for lb in approvals if lb.expected_performance]
    relevance = sum(grades) / len(grades) if grades else 2.0
    return decision, relevance, agreement


def golden_rows(
    store: Store, transcript_id: str | None = None, batch_id: str | None = None
) -> list[GoldenRow]:
    """Labeled candidates joined with their run in ``batch_id`` or the transcript's latest batch."""
    labels = store.labels.list(transcript_id=transcript_id)
    by_candidate: dict[str, list[Label]] = defaultdict(list)
    for lb in labels:
        by_candidate[lb.candidate_id].append(lb)
    runs_by_candidate: dict[str, RankingRun] = {}
    if batch_id is not None:
        for run in store.ranking.list_runs(batch_id):
            runs_by_candidate[run.candidate_id] = run
    rows: list[GoldenRow] = []
    for cid, lbs in by_candidate.items():
        candidate = store.candidates.get(cid)
        if batch_id is None:
            batches = store.ranking.list_batches(candidate.transcript_id)
            if batches and candidate.transcript_id not in runs_by_candidate:
                for run in store.ranking.list_runs(batches[-1].id):
                    runs_by_candidate.setdefault(run.candidate_id, run)
        decision, relevance, agreement = aggregate(lbs)
        effective = effective_labels(lbs)
        rows.append(
            GoldenRow(
                candidate=candidate,
                labels=effective,
                decision=decision,
                relevance=relevance,
                reviewer_agreement=agreement,
                run=runs_by_candidate.get(cid),
                provisional=all(lb.provisional for lb in effective),
            )
        )
    rows.sort(key=lambda r: (r.candidate.transcript_id, r.candidate.start_ms))
    return rows
