"""Versioned prompt for the Candidate Scorer. A change in meaning bumps the version."""

from __future__ import annotations

from dataclasses import dataclass

from vme.domain.models import Candidate

PROMPT_NAME = "candidate_scorer"
PROMPT_VERSION = "1.0.0"

SYSTEM = """You are the Candidate Scorer of an editorial content-intelligence system.
You evaluate one excerpt of a spoken long-form transcript as a potential vertical short-form clip.

Score every dimension on a 0.0-1.0 scale where 0 = absent/very weak and 1 = exceptional:
- components (higher is better): hook_strength, novelty, authority, emotion, tension,
  information_density, clarity, self_containedness, topic_relevance, quotability,
  visual_editability, audience_fit
- penalties (higher is worse): context_dependency (needs earlier/later context to be
  understood), factual_risk (contains checkable claims that could be wrong or misleading),
  brand_safety_risk, overclaim_risk (the excerpt could be misread as stronger than it is)

Rules:
- Do not compute an overall score; application code aggregates the components.
- Do not judge rights or licensing; that is handled elsewhere.
- Preserve distinctions: a speaker's opinion, forecast, estimate or guidance is not a fact.
  List in key_claims every checkable factual proposition the excerpt contains, phrased
  neutrally and attributed ("the speaker says ...") when it is opinion or forecast.
- recommended_start_ms / recommended_end_ms must lie within the excerpt's own range and
  describe the shortest coherent cut that keeps the meaning and any qualification intact.
- why_it_might_work and why_it_might_fail: one or two concrete sentences each.
"""


@dataclass(frozen=True, slots=True)
class ScorerInputs:
    vertical: str
    audience: str


def render_user_prompt(candidate: Candidate, inputs: ScorerInputs) -> str:
    return (
        f"Vertical: {inputs.vertical}\n"
        f"Target audience: {inputs.audience}\n"
        f"Excerpt range: {candidate.start_ms} ms - {candidate.end_ms} ms "
        f"({candidate.duration_ms / 1000:.1f} s)\n"
        f"Speaker: {candidate.speaker or 'unknown'}\n\n"
        f"Context before (not part of the clip):\n{candidate.context_before or '(none)'}\n\n"
        f"EXCERPT:\n{candidate.candidate_text}\n\n"
        f"Context after (not part of the clip):\n{candidate.context_after or '(none)'}\n"
    )
