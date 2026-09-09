"""Structured outputs for the Editorial Transformer (§2) and Claim Extractor (§3)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

from vme.domain.models import ClaimImportance, ClaimType


class GeneratedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    type: ClaimType
    importance: ClaimImportance
    verification_required: bool


class ExcerptPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_ms: int
    end_ms: int
    purpose: str

    @model_validator(mode="after")
    def _ordered(self) -> ExcerptPlanItem:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            msg = "excerpt plan item needs 0 <= start_ms < end_ms"
            raise ValueError(msg)
        return self


#: Card-sized text limits (characters). Cards are read, not scrolled: a 3-4 s card holds
#: about 40 words. Violations fail validation and trigger the adapter's bounded retry.
MAX_HOOK_CHARS = 110
MAX_COMMENTARY_CHARS = 280
MAX_TITLE_CHARS = 100
MAX_CTA_CHARS = 140


class EditorialDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hook: str
    commentary_before: str
    source_excerpt_plan: list[ExcerptPlanItem]
    commentary_after: str
    title_options: list[str]
    cta: str | None
    generated_claims: list[GeneratedClaim]
    transformation_summary: str

    @model_validator(mode="after")
    def _non_empty(self) -> EditorialDraft:
        if not self.hook.strip():
            msg = "hook must not be empty"
            raise ValueError(msg)
        limits = (
            ("hook", self.hook, MAX_HOOK_CHARS),
            ("commentary_before", self.commentary_before, MAX_COMMENTARY_CHARS),
            ("commentary_after", self.commentary_after, MAX_COMMENTARY_CHARS),
            ("cta", self.cta or "", MAX_CTA_CHARS),
        )
        for name, value, limit in limits:
            if len(value.strip()) > limit:
                msg = f"{name} is {len(value.strip())} characters; maximum is {limit}"
                raise ValueError(msg)
        for title in self.title_options:
            if len(title.strip()) > MAX_TITLE_CHARS:
                msg = f"title option longer than {MAX_TITLE_CHARS} characters: {title[:40]!r}..."
                raise ValueError(msg)
        if not [t for t in self.title_options if t.strip()]:
            msg = "at least one title option is required"
            raise ValueError(msg)
        if not self.source_excerpt_plan:
            msg = "source_excerpt_plan must contain at least one span"
            raise ValueError(msg)
        return self


class ClaimExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[GeneratedClaim]
