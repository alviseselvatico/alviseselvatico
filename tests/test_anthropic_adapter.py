"""Adapter behaviour with a stubbed SDK client: no network, no key."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import anthropic
import pytest
from pydantic import ValidationError

from tests.fake_llm import score_for
from vme.domain.models import LlmValidationStatus
from vme.llm.anthropic_adapter import AnthropicStructuredLlm
from vme.llm.base import LlmError, LlmRequest
from vme.ranking.schemas import CandidateScore


@dataclass
class _Usage:
    input_tokens: int = 120
    output_tokens: int = 40


@dataclass
class _Details:
    category: str | None = "cyber"


@dataclass
class _Response:
    parsed_output: Any
    model: str = "model-x-2026"
    stop_reason: str = "end_turn"
    stop_details: Any = None
    usage: _Usage = field(default_factory=_Usage)


class _Messages:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.kwargs: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.kwargs.append(kwargs)
        nxt = self.outcomes.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class _Client:
    def __init__(self, outcomes: list[Any]) -> None:
        self.messages = _Messages(outcomes)


REQ = LlmRequest(
    prompt_name="candidate_scorer",
    prompt_version="1.0.0",
    system="sys",
    user="EXCERPT:\nhello\n\nContext after",
    max_tokens=2048,
    purpose="candidate_scoring",
)


def _bad_validation() -> ValidationError:
    try:
        CandidateScore.model_validate({})
    except ValidationError as exc:
        return exc
    raise AssertionError


def test_valid_first_attempt_records_model_and_usage() -> None:
    client = _Client([_Response(parsed_output=score_for("hello"))])
    llm = AnthropicStructuredLlm(
        models={"strong": "cfg-model", "cheap": ""}, client=client, effort="low"
    )
    assert llm.has_alias("strong") and not llm.has_alias("cheap")
    out = llm.generate("strong", REQ, CandidateScore)
    assert out.ok and out.validation_status is LlmValidationStatus.VALID and out.attempts == 1
    assert (
        out.model_id_reported == "model-x-2026"
        and out.input_tokens == 120
        and out.output_tokens == 40
    )
    sent = client.messages.kwargs[0]
    assert sent["model"] == "cfg-model" and sent["output_format"] is CandidateScore
    assert sent["output_config"] == {"effort": "low"} and sent["system"] == "sys"
    assert "thinking" not in sent and "temperature" not in sent
    call = out.to_call(REQ)
    assert (
        call.provider == "anthropic" and call.model_alias == "strong" and call.response is not None
    )
    assert call.parameters["output_format"] == "CandidateScore" and call.estimated_cost_usd is None


def test_invalid_structure_is_retried_once_then_valid() -> None:
    client = _Client([_bad_validation(), _Response(parsed_output=score_for("x"))])
    llm = AnthropicStructuredLlm(models={"strong": "m"}, client=client, max_attempts=2)
    out = llm.generate("strong", REQ, CandidateScore)
    assert (
        out.ok
        and out.validation_status is LlmValidationStatus.INVALID_RETRIED
        and out.attempts == 2
    )


def test_bounded_attempts_then_failed() -> None:
    client = _Client([_bad_validation(), _bad_validation(), _bad_validation()])
    llm = AnthropicStructuredLlm(models={"strong": "m"}, client=client, max_attempts=2)
    out = llm.generate("strong", REQ, CandidateScore)
    assert not out.ok and out.validation_status is LlmValidationStatus.FAILED and out.attempts == 2
    assert out.error is not None and out.error.startswith("schema validation failed (")
    assert "Field required" in out.error  # validator verdict is persisted, not just a count
    assert len(client.messages.outcomes) == 1  # third stub never consumed


def test_refusal_is_failed_not_retried() -> None:
    client = _Client(
        [_Response(parsed_output=None, stop_reason="refusal", stop_details=_Details())]
    )
    llm = AnthropicStructuredLlm(models={"strong": "m"}, client=client, max_attempts=3)
    out = llm.generate("strong", REQ, CandidateScore)
    assert out.validation_status is LlmValidationStatus.FAILED and out.attempts == 1
    assert out.error == "refusal (category=cyber)" and out.model_id_reported == "model-x-2026"


def test_api_error_is_failed_with_type_name() -> None:
    client = _Client([anthropic.APIConnectionError(request=None)])  # type: ignore[arg-type]
    llm = AnthropicStructuredLlm(models={"strong": "m"}, client=client)
    out = llm.generate("strong", REQ, CandidateScore)
    assert out.validation_status is LlmValidationStatus.FAILED
    assert out.error is not None and out.error.startswith("APIConnectionError")


def test_unconfigured_alias_is_visible_error() -> None:
    llm = AnthropicStructuredLlm(models={"strong": "m"}, client=_Client([]))
    with pytest.raises(LlmError, match="VME_LLM_MODEL_CHEAP"):
        llm.generate("cheap", REQ, CandidateScore)
