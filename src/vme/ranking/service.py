"""Rank a transcript's candidates with the cost funnel (CLAUDE.md §12):

1. deterministic prefilter (no LLM);
2. ``cheap`` alias screens every survivor when there are more than ``finalists_k``;
3. ``strong`` alias scores the finalists; its score is decisive.

Every LLM call is persisted as an ``LlmCall`` and every candidate gets a ``RankingRun``
with its component/penalty vectors, so nothing about the ranking is an opaque number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from vme.domain.models import (
    Candidate,
    LlmCall,
    RankingBatch,
    RankingRun,
    RightsPolicy,
    new_id,
    utc_now,
)
from vme.llm.base import LlmOutcome, LlmRequest, StructuredLlm
from vme.logs import get_logger
from vme.ranking.features import PrefilterConfig, prefilter_reason, rights_risk
from vme.ranking.prompts import (
    PROMPT_NAME,
    PROMPT_VERSION,
    SYSTEM,
    ScorerInputs,
    render_user_prompt,
)
from vme.ranking.schemas import RIGHTS_RISK, CandidateScore
from vme.ranking.scoring import SCORING_VERSION, compute_score
from vme.ranking.weights import Weights
from vme.rights.gate import Action, require
from vme.storage.db import Store
from vme.storage.repositories import NotFoundError

log = get_logger("ranking")

CHEAP = "cheap"
STRONG = "strong"
PURPOSE = "candidate_scoring"


class RankingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RankingConfig:
    finalists_k: int = 5
    max_tokens: int = 8192
    vertical: str = "general"
    audience: str = "general English-speaking short-form viewers"
    prefilter: PrefilterConfig = field(default_factory=PrefilterConfig)
    created_by: str | None = None  # restrict to one segmenter version

    def __post_init__(self) -> None:
        if self.finalists_k < 1:
            msg = "finalists_k must be >= 1"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class RankingResult:
    batch: RankingBatch
    runs: list[RankingRun]  # sorted by final_score desc
    llm_calls: list[LlmCall]
    prefiltered: int
    cheap_scored: int
    strong_scored: int


@dataclass(slots=True)
class _Scored:
    candidate: Candidate
    call: LlmCall | None = None
    score: CandidateScore | None = None
    final: float = 0.0
    tier: str = "prefilter"
    reason: str = ""


def _policy(store: Store, transcript_id: str) -> RightsPolicy:
    transcript = store.transcripts.get(transcript_id)
    asset = store.media.get(transcript.media_asset_id)
    source = store.sources.get(asset.source_id)
    policy: RightsPolicy | None = None
    if source.rights_policy_id is not None:
        try:
            policy = store.policies.get(source.rights_policy_id)
        except NotFoundError:
            policy = None
    require(policy, Action.INGEST)
    assert policy is not None  # noqa: S101 - require() raised otherwise
    return policy


def _score_one(
    llm: StructuredLlm,
    alias: str,
    candidate: Candidate,
    config: RankingConfig,
    weights: Weights,
    risk: float,
) -> tuple[LlmOutcome[CandidateScore], LlmCall, float]:
    request = LlmRequest(
        prompt_name=PROMPT_NAME,
        prompt_version=PROMPT_VERSION,
        system=SYSTEM,
        user=render_user_prompt(candidate, ScorerInputs(config.vertical, config.audience)),
        max_tokens=config.max_tokens,
        purpose=PURPOSE,
        input_artifact_refs=(f"candidate:{candidate.id}", f"transcript:{candidate.transcript_id}"),
    )
    outcome = llm.generate(alias, request, CandidateScore)
    call = outcome.to_call(request)
    final = 0.0
    if outcome.parsed is not None:
        penalties = {**outcome.parsed.llm_penalties(), RIGHTS_RISK: risk}
        final = compute_score(outcome.parsed.components(), penalties, weights).final
    return outcome, call, final


def rank_transcript(
    store: Store,
    transcript_id: str,
    llm: StructuredLlm,
    weights: Weights,
    config: RankingConfig | None = None,
    *,
    now: datetime | None = None,
) -> RankingResult:
    config = config or RankingConfig()
    now = now or utc_now()
    policy = _policy(store, transcript_id)
    risk = rights_risk(policy)
    candidates = store.candidates.list(transcript_id, created_by=config.created_by)
    if not candidates:
        msg = f"transcript {transcript_id!r} has no candidates; run `segment` first"
        raise RankingError(msg)
    if not llm.has_alias(STRONG):
        msg = "LLM alias 'strong' is not configured (VME_LLM_MODEL_STRONG)"
        raise RankingError(msg)

    scored: list[_Scored] = []
    survivors: list[_Scored] = []
    for c in candidates:
        reason = prefilter_reason(c, config.prefilter)
        entry = _Scored(candidate=c)
        if reason is None:
            survivors.append(entry)
        else:
            entry.reason = f"prefilter: {reason}"
        scored.append(entry)
    prefiltered = len(scored) - len(survivors)
    calls: list[LlmCall] = []

    # Stage 2: cheap screening only when it can actually narrow the field.
    cheap_scored = 0
    if len(survivors) > config.finalists_k:
        if not llm.has_alias(CHEAP):
            msg = (
                f"{len(survivors)} candidates exceed finalists_k={config.finalists_k} but "
                "LLM alias 'cheap' is not configured (VME_LLM_MODEL_CHEAP)"
            )
            raise RankingError(msg)
        for entry in survivors:
            outcome, call, final = _score_one(llm, CHEAP, entry.candidate, config, weights, risk)
            calls.append(call)
            entry.call, entry.score, entry.final, entry.tier = call, outcome.parsed, final, CHEAP
            entry.reason = "" if outcome.ok else f"llm_failed: {outcome.error}"
            cheap_scored += 1
        ranked = sorted(
            (e for e in survivors if e.score is not None),
            key=lambda e: (-e.final, e.candidate.start_ms),
        )
        finalists = ranked[: config.finalists_k]
    else:
        finalists = list(survivors)

    # Stage 3: strong scoring of finalists (decisive).
    strong_scored = 0
    for entry in finalists:
        outcome, call, final = _score_one(llm, STRONG, entry.candidate, config, weights, risk)
        calls.append(call)
        strong_scored += 1
        if outcome.ok:
            entry.call, entry.score, entry.final, entry.tier = call, outcome.parsed, final, STRONG
            entry.reason = ""
        elif entry.score is None:
            entry.call, entry.tier = call, STRONG
            entry.reason = f"llm_failed: {outcome.error}"
        else:  # keep the cheap score but record the failed strong attempt in the rationale
            entry.reason = f"strong_failed_kept_cheap: {outcome.error}"

    batch = RankingBatch(
        id=new_id("rkb"),
        transcript_id=transcript_id,
        scoring_version=SCORING_VERSION,
        weights_version=weights.version,
        prompt_version=PROMPT_VERSION,
        model_alias=STRONG,
        created_at=now,
    )
    runs: list[RankingRun] = []
    for entry in scored:
        if entry.score is not None:
            rationale = (
                f"[{entry.tier}] {entry.score.why_it_might_work} | "
                f"risks: {entry.score.why_it_might_fail}"
            )
            if entry.reason:
                rationale = f"{rationale} | {entry.reason}"
            features = entry.score.components()
            risks = {**entry.score.llm_penalties(), RIGHTS_RISK: risk}
        else:
            rationale = f"[{entry.tier}] {entry.reason}"
            features = {}
            risks = {RIGHTS_RISK: risk}
        runs.append(
            RankingRun(
                id=new_id("rkr"),
                ranking_batch_id=batch.id,
                candidate_id=entry.candidate.id,
                features=features,
                risks=risks,
                final_score=entry.final,
                rationale=rationale,
                llm_call_id=entry.call.id if entry.call else None,
                created_at=now,
            )
        )
    runs.sort(key=lambda r: (-r.final_score, r.candidate_id))
    with store.transaction():
        for call in calls:
            store.llm_calls.add(call)
        store.ranking.add_batch(batch)
        store.ranking.add_runs(runs)
    log.info(
        "ranking_batch_created",
        extra={
            "transcript_id": transcript_id,
            "batch_id": batch.id,
            "scoring_version": batch.scoring_version,
            "weights_version": batch.weights_version,
            "prompt_version": batch.prompt_version,
            "candidates": len(candidates),
            "prefiltered": prefiltered,
            "cheap_scored": cheap_scored,
            "strong_scored": strong_scored,
            "llm_calls": len(calls),
            "failed_calls": sum(1 for c in calls if c.validation_status == "FAILED"),
            "input_tokens": sum(c.input_tokens or 0 for c in calls),
            "output_tokens": sum(c.output_tokens or 0 for c in calls),
            "top_scores": [r.final_score for r in runs[:5]],
        },
    )
    return RankingResult(
        batch=batch,
        runs=runs,
        llm_calls=calls,
        prefiltered=prefiltered,
        cheap_scored=cheap_scored,
        strong_scored=strong_scored,
    )
