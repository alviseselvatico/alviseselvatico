"""Automated claim evaluation (PROMPT_CONTRACTS §4, guardrails §7) with the D030 policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from vme.domain.models import (
    Claim,
    ClaimImportance,
    ClaimStatus,
    Evidence,
    LlmCall,
    ReviewEvent,
    new_id,
    utc_now,
)
from vme.editorial.review import recheck
from vme.factcheck.evidence import EvidenceRetriever, RetrievalResult
from vme.llm.base import LlmRequest, StructuredLlm
from vme.logs import get_logger
from vme.storage.db import Store

log = get_logger("factcheck.evaluator")

EVALUATOR_PROMPT_NAME = "factcheck_evaluator"
EVALUATOR_PROMPT_VERSION = "1.0.0"
EVALUATOR_ID = f"{EVALUATOR_PROMPT_NAME}:{EVALUATOR_PROMPT_VERSION}"

EVALUATOR_SYSTEM = """You are the Fact Check Evaluator of an editorial system. You receive one claim
written by the system, the source excerpt it accompanies, and a numbered list of evidence
items retrieved from the web. Decide:

- SUPPORTED: at least one evidence item states the same proposition, from an identifiable
  source with a date, and the claim has not been strengthened beyond what the evidence says.
- CONTRADICTED: reliable evidence states the opposite or a materially different fact.
- AMBIGUOUS: evidence points both ways, or the claim is true only with a qualification the
  text does not carry (state the qualification in qualification_needed).
- INSUFFICIENT: no evidence item actually bears on the proposition. Say so; never infer
  support from general plausibility or from the source excerpt alone.

supporting_evidence_ids must list only the item ids that support your status. A search
snippet is weaker than primary material: prefer official records, the original document or
speech, and reputable reference works. Be brief and concrete in reason."""


class VerdictStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    AMBIGUOUS = "AMBIGUOUS"
    INSUFFICIENT = "INSUFFICIENT"


class FactCheckVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: VerdictStatus
    reason: str
    supporting_evidence_ids: list[str]
    qualification_needed: str | None


@dataclass(frozen=True, slots=True)
class EvaluationPolicy:
    """D030: which importances may be resolved by the machine."""

    human_confirmation_for: frozenset[ClaimImportance] = frozenset({ClaimImportance.HIGH})


@dataclass(frozen=True, slots=True)
class ClaimEvaluation:
    claim: Claim
    verdict: FactCheckVerdict | None
    evidence: list[Evidence]
    calls: list[LlmCall]
    applied_status: ClaimStatus
    note: str


def _render(claim: Claim, context: str, excerpt: str, retrieval: RetrievalResult) -> str:
    lines = [
        f"CLAIM ({claim.claim_type.value}, importance {claim.importance.value}):\n"
        f"{claim.claim_text}\n",
        f"WHERE IT APPEARS (system-written text):\n{context}\n",
        f"SOURCE EXCERPT (verbatim, what the speaker said):\n{excerpt}\n",
        "EVIDENCE ITEMS:",
    ]
    if not retrieval.items:
        lines.append("(none retrieved)")
    for i, item in enumerate(retrieval.items, start=1):
        lines.append(
            f"[E{i}] {item.title or '(untitled)'} | {item.url} | "
            f"date: {item.published or 'unknown'}\n     {item.snippet or '(no excerpt)'}"
        )
    if retrieval.summary:
        lines.append(f"\nRESEARCHER SUMMARY (not evidence by itself):\n{retrieval.summary}")
    return "\n".join(lines)


def evaluate_claim(
    store: Store,
    claim_id: str,
    *,
    retriever: EvidenceRetriever,
    llm: StructuredLlm,
    alias: str = "strong",
    policy: EvaluationPolicy | None = None,
    max_tokens: int = 4096,
    now: datetime | None = None,
) -> ClaimEvaluation:
    """Retrieve evidence, evaluate, persist evidence + calls, apply the D030 policy."""
    policy = policy or EvaluationPolicy()
    now = now or utc_now()
    claim = store.claims.get(claim_id)
    if claim.status is not ClaimStatus.UNVERIFIED:
        return ClaimEvaluation(claim, None, [], [], claim.status, "already resolved; skipped")
    draft = store.editorial.get(claim.editorial_version_id)
    candidate = store.candidates.get(draft.candidate_id)
    context = (
        f"Hook: {draft.hook}\nBefore: {draft.commentary_before}\n"
        f"After: {draft.commentary_after}\nTitles: {' | '.join(draft.title_options)}\n"
        f"CTA: {draft.cta or ''}"
    )

    retrieval = retriever.retrieve(claim.claim_text, context)
    calls: list[LlmCall] = [retrieval.call] if retrieval.call else []
    evidence = [
        Evidence(
            id=new_id("evd"),
            claim_id=claim.id,
            retriever=retrieval.retriever,
            url=item.url,
            title=item.title,
            snippet=item.snippet,
            published=item.published,
            retrieved_at=now,
            llm_call_id=retrieval.call.id if retrieval.call else None,
        )
        for item in retrieval.items
    ]
    if retrieval.call is not None and retrieval.call.error:
        with store.transaction():
            store.llm_calls.add(retrieval.call)
        return ClaimEvaluation(
            claim, None, [], calls, claim.status, f"retrieval failed: {retrieval.call.error}"
        )

    request = LlmRequest(
        prompt_name=EVALUATOR_PROMPT_NAME,
        prompt_version=EVALUATOR_PROMPT_VERSION,
        system=EVALUATOR_SYSTEM,
        user=_render(claim, context, candidate.candidate_text, retrieval),
        max_tokens=max_tokens,
        purpose="factcheck_evaluation",
        input_artifact_refs=(f"claim:{claim.id}", f"editorial_version:{draft.id}"),
    )
    outcome = llm.generate(alias, request, FactCheckVerdict)
    call = outcome.to_call(request)
    calls.append(call)
    if outcome.parsed is None:
        with store.transaction():
            for c in calls:
                store.llm_calls.add(c)
            if evidence:
                store.evidence.add_many(evidence)
        return ClaimEvaluation(
            claim, None, evidence, calls, claim.status, f"evaluation failed: {outcome.error}"
        )

    verdict = outcome.parsed
    valid_ids = {f"E{i}" for i in range(1, len(evidence) + 1)}
    supporting = [s for s in verdict.supporting_evidence_ids if s in valid_ids]
    machine = verdict.status
    if machine is VerdictStatus.SUPPORTED and not supporting:
        machine = VerdictStatus.INSUFFICIENT  # guardrails §7: support needs evidence that supports
    applied, note = _apply_policy(claim, machine, verdict, policy)
    event = ReviewEvent(
        id=new_id("rev"),
        object_type="claim",
        object_id=claim.id,
        decision=f"machine_{machine.value.lower()}",
        reason_codes=supporting,
        notes=verdict.reason,
        reviewer=EVALUATOR_ID,
        created_at=now,
    )
    with store.transaction():
        for c in calls:
            store.llm_calls.add(c)
        if evidence:
            store.evidence.add_many(evidence)
        store.reviews.add(event)
        claim = store.claims.set_machine_verdict(
            claim.id,
            status=applied,
            machine_status=machine.value,
            machine_reason=verdict.reason,
            evaluated_at=now,
            confidence=None,
            note=note if applied is ClaimStatus.UNVERIFIED else None,
        )
    log.info(
        "claim_evaluated",
        extra={
            "claim_id": claim.id,
            "machine_status": machine.value,
            "applied_status": applied.value,
            "evidence": len(evidence),
            "supporting": supporting,
            "searches": retrieval.searches,
        },
    )
    recheck(store, draft.id, reviewer=EVALUATOR_ID, now=now)
    return ClaimEvaluation(claim, verdict, evidence, calls, applied, note)


def _apply_policy(
    claim: Claim, machine: VerdictStatus, verdict: FactCheckVerdict, policy: EvaluationPolicy
) -> tuple[ClaimStatus, str]:
    if machine is VerdictStatus.CONTRADICTED:
        return ClaimStatus.CONTRADICTED, "contradicted by evidence"
    if machine is VerdictStatus.AMBIGUOUS:
        q = verdict.qualification_needed or ""
        return ClaimStatus.AMBIGUOUS, f"ambiguous; qualification: {q}".strip()
    if machine is VerdictStatus.INSUFFICIENT:
        return ClaimStatus.UNVERIFIED, "insufficient evidence; human review required"
    if claim.importance in policy.human_confirmation_for:
        return (
            ClaimStatus.UNVERIFIED,
            f"machine says SUPPORTED but importance {claim.importance.value} "
            "requires human confirmation (D030)",
        )
    return ClaimStatus.SUPPORTED, "supported by evidence"
