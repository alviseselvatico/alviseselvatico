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
