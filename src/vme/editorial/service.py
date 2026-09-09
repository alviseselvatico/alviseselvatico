"""Generate an editorial draft for a candidate and its claims (D010, D014).

Two structured calls: the Editorial Transformer (``strong``) writes the draft and lists the
claims it knows it introduced; the Claim Extractor (``cheap`` when configured, else
``strong``) independently re-reads the generated text. The union of both lists becomes
``Claim`` rows, all ``UNVERIFIED``. A draft with at least one claim starts in
``blocked_factcheck``; otherwise it goes straight to ``needs_review``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from vme.domain.models import (
    Candidate,
    Claim,
    ClaimStatus,
    DraftStatus,
    EditorialVersion,
    ExcerptSpan,
    LlmCall,
    RightsPolicy,
    new_id,
    utc_now,
)
from vme.editorial.prompts import (
    CLAIMS_PROMPT_NAME,
    CLAIMS_PROMPT_VERSION,
    CLAIMS_SYSTEM,
    EDITORIAL_PROMPT_NAME,
    EDITORIAL_PROMPT_VERSION,
    EDITORIAL_SYSTEM,
    EditorialInputs,
    render_claims_prompt,
    render_editorial_prompt,
)
from vme.editorial.schemas import ClaimExtraction, EditorialDraft, GeneratedClaim
from vme.llm.base import LlmRequest, StructuredLlm
from vme.logs import get_logger
from vme.rights.gate import Action, require
from vme.storage.db import Store
from vme.storage.repositories import NotFoundError

log = get_logger("editorial")

STRONG = "strong"
CHEAP = "cheap"


class EditorialError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EditorialConfig:
    vertical: str = "general"
    audience: str = "general English-speaking short-form viewers"
    target_ms: int = 45_000
    max_tokens: int = 8192
    use_ranking: bool = True


@dataclass(frozen=True, slots=True)
class EditorialResult:
    draft: EditorialVersion
    claims: list[Claim]
    llm_calls: list[LlmCall]


def policy_for_candidate(store: Store, candidate: Candidate) -> RightsPolicy | None:
    transcript = store.transcripts.get(candidate.transcript_id)
    asset = store.media.get(transcript.media_asset_id)
    source = store.sources.get(asset.source_id)
    if source.rights_policy_id is None:
        return None
    try:
        return store.policies.get(source.rights_policy_id)
    except NotFoundError:
        return None


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def _latest_ranking(store: Store, candidate: Candidate) -> tuple[str | None, tuple[str, ...]]:
    batches = store.ranking.list_batches(candidate.transcript_id)
    if not batches:
        return None, ()
    for run in store.ranking.list_runs(batches[-1].id):
        if run.candidate_id == candidate.id and run.llm_call_id:
            call = store.llm_calls.get(run.llm_call_id)
            claims = tuple(str(c) for c in (call.response or {}).get("key_claims", []))
            return run.rationale, claims
    return None, ()


def _clip_spans(draft: EditorialDraft, candidate: Candidate) -> list[ExcerptSpan]:
    spans: list[ExcerptSpan] = []
    for item in draft.source_excerpt_plan:
        start = max(candidate.start_ms, item.start_ms)
        end = min(candidate.end_ms, item.end_ms)
        if end <= start:
            msg = (
                f"excerpt plan span {item.start_ms}-{item.end_ms} ms lies outside the candidate "
                f"range {candidate.start_ms}-{candidate.end_ms} ms"
            )
            raise EditorialError(msg)
        spans.append(ExcerptSpan(start_ms=start, end_ms=end, purpose=item.purpose))
    return spans


def generate_editorial(
    store: Store,
    candidate_id: str,
    llm: StructuredLlm,
    config: EditorialConfig | None = None,
    *,
    now: datetime | None = None,
) -> EditorialResult:
    config = config or EditorialConfig()
    now = now or utc_now()
    candidate = store.candidates.get(candidate_id)
    policy = policy_for_candidate(store, candidate)
    require(policy, Action.RENDER_TRANSFORM, now=now)
    assert policy is not None  # noqa: S101 - require() raised otherwise
    if not llm.has_alias(STRONG):
        msg = "LLM alias 'strong' is not configured (VME_LLM_MODEL_STRONG)"
        raise EditorialError(msg)

    rationale, key_claims = _latest_ranking(store, candidate) if config.use_ranking else (None, ())
    inputs = EditorialInputs(
        vertical=config.vertical,
        audience=config.audience,
        target_ms=config.target_ms,
        ranking_rationale=rationale,
        key_claims=key_claims,
    )
    refs = (
        f"candidate:{candidate.id}",
        f"transcript:{candidate.transcript_id}",
        f"policy:{policy.id}",
    )
    calls: list[LlmCall] = []

    edit_req = LlmRequest(
        prompt_name=EDITORIAL_PROMPT_NAME,
        prompt_version=EDITORIAL_PROMPT_VERSION,
        system=EDITORIAL_SYSTEM,
        user=render_editorial_prompt(candidate, policy, inputs),
        max_tokens=config.max_tokens,
        purpose="editorial_transform",
        input_artifact_refs=refs,
    )
    edit_out = llm.generate(STRONG, edit_req, EditorialDraft)
    edit_call = edit_out.to_call(edit_req)
    calls.append(edit_call)
    if edit_out.parsed is None:
        with store.transaction():
            store.llm_calls.add(edit_call)
        msg = f"editorial generation failed: {edit_out.error}"
        raise EditorialError(msg)
    draft_out = edit_out.parsed
    spans = _clip_spans(draft_out, candidate)

    titles = [t.strip() for t in draft_out.title_options if t.strip()]
    claims_alias = CHEAP if llm.has_alias(CHEAP) else STRONG
    claims_req = LlmRequest(
        prompt_name=CLAIMS_PROMPT_NAME,
        prompt_version=CLAIMS_PROMPT_VERSION,
        system=CLAIMS_SYSTEM,
        user=render_claims_prompt(
            candidate,
            draft_out.hook,
            draft_out.commentary_before,
            draft_out.commentary_after,
            titles,
            draft_out.cta,
        ),
        max_tokens=config.max_tokens,
        purpose="claim_extraction",
        input_artifact_refs=refs,
    )
    claims_out = llm.generate(claims_alias, claims_req, ClaimExtraction)
    claims_call = claims_out.to_call(claims_req)
    calls.append(claims_call)
    if claims_out.parsed is None:
        with store.transaction():
            for call in calls:
                store.llm_calls.add(call)
        msg = f"claim extraction failed: {claims_out.error}"
        raise EditorialError(msg)

    draft = EditorialVersion(
        id=new_id("edv"),
        candidate_id=candidate.id,
        version=store.editorial.next_version(candidate.id),
        hook=draft_out.hook.strip(),
        commentary_before=draft_out.commentary_before.strip(),
        commentary_after=draft_out.commentary_after.strip(),
        excerpt_plan=spans,
        title=titles[0],
        title_options=titles,
        cta=(draft_out.cta or "").strip() or None,
        transformation_summary=draft_out.transformation_summary.strip(),
        status=DraftStatus.GENERATED,
        prompt_version=EDITORIAL_PROMPT_VERSION,
        model_alias=STRONG,
        llm_call_id=edit_call.id,
        created_at=now,
    )

    # Union of both claim lists, de-duplicated on normalized text. Every claim is UNVERIFIED.
    seen: dict[str, Claim] = {}

    def _add(items: list[GeneratedClaim], origin: str) -> None:
        for g in items:
            key = _normalize(g.text)
            if not key or key in seen:
                continue
            seen[key] = Claim(
                id=new_id("clm"),
                editorial_version_id=draft.id,
                claim_text=g.text.strip(),
                claim_type=g.type,
                importance=g.importance,
                status=ClaimStatus.UNVERIFIED,
                origin=origin,
                created_at=now,
            )

    _add(draft_out.generated_claims, f"{EDITORIAL_PROMPT_NAME}:{EDITORIAL_PROMPT_VERSION}")
    _add(claims_out.parsed.claims, f"{CLAIMS_PROMPT_NAME}:{CLAIMS_PROMPT_VERSION}")
    claims = list(seen.values())
    initial = DraftStatus.BLOCKED_FACTCHECK if claims else DraftStatus.NEEDS_REVIEW

    with store.transaction():
        for call in calls:
            store.llm_calls.add(call)
        store.editorial.add(draft)
        if claims:
            store.claims.add_many(claims)
        draft = store.editorial.set_status(draft.id, initial)
    log.info(
        "editorial_generated",
        extra={
            "candidate_id": candidate.id,
            "editorial_version_id": draft.id,
            "version": draft.version,
            "status": draft.status.value,
            "claims": len(claims),
            "claims_from_transformer": len(draft_out.generated_claims),
            "claims_from_extractor": len(claims_out.parsed.claims),
            "prompt_version": draft.prompt_version,
            "llm_calls": [c.id for c in calls],
            "input_tokens": sum(c.input_tokens or 0 for c in calls),
            "output_tokens": sum(c.output_tokens or 0 for c in calls),
        },
    )
    return EditorialResult(draft=draft, claims=claims, llm_calls=calls)
