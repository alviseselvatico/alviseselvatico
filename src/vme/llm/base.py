"""Provider-agnostic contract for structured (schema-validated) generation.

Aliases (``cheap``/``strong``) are resolved to provider model IDs by the adapter from
configuration; application code never sees a model ID except as ``model_id_reported``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from vme.domain.models import LlmCall, LlmValidationStatus, new_id, utc_now

T = TypeVar("T", bound=BaseModel)


class LlmError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LlmRequest:
    prompt_name: str
    prompt_version: str
    system: str
    user: str
    max_tokens: int
    purpose: str
    input_artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LlmOutcome[T: BaseModel]:
    """Everything needed to persist an :class:`LlmCall` plus the parsed object."""

    provider: str
    model_alias: str
    model_id_reported: str | None
    parsed: T | None
    response: dict[str, Any] | None
    validation_status: LlmValidationStatus
    attempts: int
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    parameters: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.parsed is not None and self.validation_status is not LlmValidationStatus.FAILED

    def to_call(self, request: LlmRequest) -> LlmCall:
        return LlmCall(
            id=new_id("llm"),
            purpose=request.purpose,
            input_artifact_refs=list(request.input_artifact_refs),
            prompt_name=request.prompt_name,
            prompt_version=request.prompt_version,
            provider=self.provider,
            model_alias=self.model_alias,
            model_id_reported=self.model_id_reported,
            parameters=self.parameters,
            response=self.response,
            validation_status=self.validation_status,
            attempts=self.attempts,
            latency_ms=self.latency_ms,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            estimated_cost_usd=None,  # pricing is not encoded in Phase 0 code
            error=self.error,
            created_at=utc_now(),
        )


class StructuredLlm(Protocol):
    @property
    def provider(self) -> str: ...

    def has_alias(self, alias: str) -> bool: ...

    def generate[T: BaseModel](
        self, alias: str, request: LlmRequest, schema: type[T]
    ) -> LlmOutcome[T]: ...
