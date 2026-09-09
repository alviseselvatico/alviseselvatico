"""Anthropic Messages API adapter using structured outputs (D012).

``client.messages.parse(..., output_format=Schema)`` makes the API constrain the reply to
the schema and the SDK validate it into the Pydantic model. Thinking is left at the
model's default (adaptive on current models; no ``budget_tokens``, no ``temperature``).
Invalid structure is retried a bounded number of times (ARCHITECTURE §6); refusals and
API errors are recorded as ``FAILED``, never hidden.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from vme.domain.models import LlmValidationStatus
from vme.llm.base import LlmError, LlmOutcome, LlmRequest
from vme.logs import get_logger

log = get_logger("llm.anthropic")

PROVIDER = "anthropic"


class AnthropicStructuredLlm:
    def __init__(
        self,
        *,
        models: Mapping[str, str],
        api_key: str | None = None,
        max_attempts: int = 2,
        effort: str | None = None,
        client: Any | None = None,
    ) -> None:
        self._models = {alias: model_id for alias, model_id in models.items() if model_id}
        self._api_key = api_key or None
        self._max_attempts = max(1, max_attempts)
        self._effort = effort or None
        self._client = client

    @property
    def provider(self) -> str:
        return PROVIDER

    def has_alias(self, alias: str) -> bool:
        return alias in self._models

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - dependency is declared
                msg = "anthropic SDK is not installed"
                raise LlmError(msg) from exc
            # api_key=None lets the SDK resolve ANTHROPIC_API_KEY / auth profile itself.
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def generate[T: BaseModel](
        self, alias: str, request: LlmRequest, schema: type[T]
    ) -> LlmOutcome[T]:
        model_id = self._models.get(alias)
        if not model_id:
            msg = f"no model configured for alias {alias!r} (set VME_LLM_MODEL_{alias.upper()})"
            raise LlmError(msg)
        import anthropic

        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": model_id,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
            "output_format": schema,
        }
        if self._effort:
            kwargs["output_config"] = {"effort": self._effort}
        parameters: dict[str, Any] = {
            "max_tokens": request.max_tokens,
            "effort": self._effort,
            "output_format": schema.__name__,
            "max_attempts": self._max_attempts,
        }

        started = time.perf_counter()
        attempts = 0
        last_error: str | None = None
        model_reported: str | None = None
        in_tok: int | None = None
        out_tok: int | None = None
        while attempts < self._max_attempts:
            attempts += 1
            try:
                response = client.messages.parse(**kwargs)
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
                # The SDK already retried transient errors; report and stop.
                last_error = f"{type(exc).__name__}: {exc}"
                break
            except ValidationError as exc:
                last_error = f"schema validation failed: {exc.error_count()} error(s)"
                log.warning("llm_invalid_structure", extra={"alias": alias, "attempt": attempts})
                continue
            model_reported = getattr(response, "model", None)
            usage = getattr(response, "usage", None)
            in_tok = getattr(usage, "input_tokens", None)
            out_tok = getattr(usage, "output_tokens", None)
            if response.stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                category = getattr(details, "category", None)
                last_error = f"refusal (category={category})"
                break
            parsed = response.parsed_output
            if parsed is None:
                last_error = f"no parsed output (stop_reason={response.stop_reason})"
                log.warning("llm_no_parsed_output", extra={"alias": alias, "attempt": attempts})
                continue
            latency = round((time.perf_counter() - started) * 1000)
            status = (
                LlmValidationStatus.VALID if attempts == 1 else LlmValidationStatus.INVALID_RETRIED
            )
            return LlmOutcome(
                provider=PROVIDER,
                model_alias=alias,
                model_id_reported=model_reported,
                parsed=parsed,
                response=parsed.model_dump(mode="json"),
                validation_status=status,
                attempts=attempts,
                latency_ms=latency,
                input_tokens=in_tok,
                output_tokens=out_tok,
                parameters=parameters,
            )
        latency = round((time.perf_counter() - started) * 1000)
        log.error("llm_failed", extra={"alias": alias, "attempts": attempts, "error": last_error})
        return LlmOutcome(
            provider=PROVIDER,
            model_alias=alias,
            model_id_reported=model_reported,
            parsed=None,
            response=None,
            validation_status=LlmValidationStatus.FAILED,
            attempts=attempts,
            latency_ms=latency,
            input_tokens=in_tok,
            output_tokens=out_tok,
            parameters=parameters,
            error=last_error,
        )
