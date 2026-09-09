"""Versioned prompts: editorial_transformer 1.0.0 and claim_extractor 1.0.0."""

from __future__ import annotations

from dataclasses import dataclass

from vme.domain.models import Candidate, RightsPolicy

EDITORIAL_PROMPT_NAME = "editorial_transformer"
EDITORIAL_PROMPT_VERSION = "1.0.0"
CLAIMS_PROMPT_NAME = "claim_extractor"
CLAIMS_PROMPT_VERSION = "1.0.0"

EDITORIAL_SYSTEM = """You are the Editorial Transformer of a content-intelligence system that turns
excerpts of authorized long-form speech into short vertical videos with ORIGINAL editorial value.
Phase 0 output is text only: a hook card, commentary text shown before/after the excerpt,
title options and an optional call to action. There is no narration and no synthetic voice.

Sizes (hard limits, the output is rejected otherwise): hook <= 110 characters;
commentary_before <= 280 characters; commentary_after <= 280 characters; each title <= 100
characters; cta <= 140 characters. Cards are on screen for a few seconds: write tight.

Rules (mandatory):
- Add value through context, explanation, analysis or synthesis. Cropping plus captions plus a
  generic hook is not enough.
- Never invent facts, numbers, names or quotations. Never fabricate a quotation.
- Preserve the speaker's framing: an opinion stays an opinion, a forecast stays a forecast,
  guidance stays guidance, a reported claim stays reported. "Personally, I think revenue could
  double" must never become "Company expects revenue to double".
- Do not add sensational certainty to an ambiguous source. No generic filler.
- source_excerpt_plan: spans in milliseconds inside the excerpt's own range that will be shown,
  in order, with a short purpose. Never cut a qualification that reverses or weakens a claim.
- generated_claims: list EVERY checkable factual proposition that YOUR hook, commentary, titles
  or CTA introduce or materially restate (not what the speaker says inside the excerpt).
  Classify each as FACT, OPINION_ATTRIBUTION, FORECAST, ESTIMATE, GUIDANCE, ALLEGATION or
  HYPOTHETICAL with importance LOW/MEDIUM/HIGH; verification_required is true unless the
  statement is a pure attribution of what the excerpt itself says.
- Respect the transformation policy in the input (attribution text, review requirements).
- transformation_summary: one or two sentences on what original value was added.
"""

CLAIMS_SYSTEM = """You are the Claim Extractor of a content-intelligence system.
You receive text written by an editorial system (hook, commentary, titles, call to action) and
the source excerpt it accompanies. Extract ONLY claims introduced or materially restated by the
editorial text, not statements that appear inside the source excerpt itself.

A claim is any proposition a careful fact-checker would want to verify: numbers, dates, names,
causal statements, comparisons, characterisations of what a person or company said or did.
Classify each as FACT, OPINION_ATTRIBUTION, FORECAST, ESTIMATE, GUIDANCE, ALLEGATION or
HYPOTHETICAL, with importance LOW/MEDIUM/HIGH. Set verification_required to false only for a
faithful attribution of what the excerpt itself says. Return an empty list when there is none.
"""


@dataclass(frozen=True, slots=True)
class EditorialInputs:
    vertical: str
    audience: str
    target_ms: int
    ranking_rationale: str | None = None
    key_claims: tuple[str, ...] = ()


def _policy_block(policy: RightsPolicy) -> str:
    lines = [f"- rights basis: {policy.basis_type.value}"]
    lines.append(
        f"- heightened human rights review required: {'yes' if policy.review_required else 'no'}"
    )
    if policy.requires_attribution and policy.attribution_text:
        lines.append(f"- required attribution text: {policy.attribution_text}")
    if policy.territory_notes:
        lines.append(f"- territory notes: {policy.territory_notes}")
    return "\n".join(lines)


def render_editorial_prompt(
    candidate: Candidate, policy: RightsPolicy, inputs: EditorialInputs
) -> str:
    extra = ""
    if inputs.ranking_rationale:
        extra += f"\nRanking rationale (from the scorer):\n{inputs.ranking_rationale}\n"
    if inputs.key_claims:
        extra += "\nClaims the scorer noticed inside the excerpt (source statements, not yours):\n"
        extra += "\n".join(f"- {c}" for c in inputs.key_claims) + "\n"
    return (
        f"Vertical: {inputs.vertical}\n"
        f"Target audience: {inputs.audience}\n"
        f"Target clip duration: about {inputs.target_ms / 1000:.0f} s\n"
        f"Excerpt range: {candidate.start_ms} ms - {candidate.end_ms} ms "
        f"({candidate.duration_ms / 1000:.1f} s)\n"
        f"Speaker: {candidate.speaker or 'unknown'}\n\n"
        f"Permitted transformation policy:\n{_policy_block(policy)}\n"
        f"{extra}\n"
        f"Context before (not shown in the clip):\n{candidate.context_before or '(none)'}\n\n"
        f"SOURCE EXCERPT (verbatim transcript):\n{candidate.candidate_text}\n\n"
        f"Context after (not shown in the clip):\n{candidate.context_after or '(none)'}\n"
    )


def render_claims_prompt(
    candidate: Candidate, hook: str, before: str, after: str, titles: list[str], cta: str | None
) -> str:
    return (
        "EDITORIAL TEXT (written by the system):\n"
        f"Hook: {hook}\n"
        f"Commentary before: {before or '(none)'}\n"
        f"Commentary after: {after or '(none)'}\n"
        f"Title options: {' | '.join(titles)}\n"
        f"CTA: {cta or '(none)'}\n\n"
        f"SOURCE EXCERPT (verbatim, not to be extracted):\n{candidate.candidate_text}\n"
    )
