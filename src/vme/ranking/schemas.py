"""Structured output of the Candidate Scorer (docs/PROMPT_CONTRACTS.md §1).

Ranges are checked by validators rather than JSON-schema constraints so the schema sent
to the provider stays plain (object/number/string/array), and an out-of-range value is a
visible validation failure that triggers the bounded retry.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

COMPONENTS: tuple[str, ...] = (
    "hook_strength",
    "novelty",
    "authority",
    "emotion",
    "tension",
    "information_density",
    "clarity",
    "self_containedness",
    "topic_relevance",
    "quotability",
    "visual_editability",
    "audience_fit",
)

LLM_PENALTIES: tuple[str, ...] = (
    "context_dependency",
    "factual_risk",
    "brand_safety_risk",
    "overclaim_risk",
)

#: Derived by application code from the rights policy, never asked to the LLM.
RIGHTS_RISK = "rights_risk"
PENALTIES: tuple[str, ...] = (*LLM_PENALTIES, RIGHTS_RISK)


class CandidateScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hook_strength: float
    novelty: float
    authority: float
    emotion: float
    tension: float
    information_density: float
    clarity: float
    self_containedness: float
    topic_relevance: float
    quotability: float
    visual_editability: float
    audience_fit: float
    context_dependency: float
    factual_risk: float
    brand_safety_risk: float
    overclaim_risk: float
    recommended_start_ms: int
    recommended_end_ms: int
    why_it_might_work: str
    why_it_might_fail: str
    key_claims: list[str]

    @model_validator(mode="after")
    def _ranges(self) -> CandidateScore:
        for name in (*COMPONENTS, *LLM_PENALTIES):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                msg = f"{name} must be within [0, 1], got {value}"
                raise ValueError(msg)
        if self.recommended_start_ms < 0 or self.recommended_end_ms <= self.recommended_start_ms:
            msg = "recommended_end_ms must be > recommended_start_ms >= 0"
            raise ValueError(msg)
        return self

    def components(self) -> dict[str, float]:
        return {name: float(getattr(self, name)) for name in COMPONENTS}

    def llm_penalties(self) -> dict[str, float]:
        return {name: float(getattr(self, name)) for name in LLM_PENALTIES}
