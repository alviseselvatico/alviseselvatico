"""Resolve the configured LLM provider and its alias -> model map (D012)."""

from __future__ import annotations

from vme.config import Settings
from vme.llm.anthropic_adapter import PROVIDER as ANTHROPIC
from vme.llm.anthropic_adapter import AnthropicStructuredLlm
from vme.llm.base import LlmError, StructuredLlm


def build_llm(settings: Settings) -> StructuredLlm:
    if settings.llm_provider == ANTHROPIC:
        return AnthropicStructuredLlm(
            models={"cheap": settings.llm_model_cheap, "strong": settings.llm_model_strong},
            api_key=settings.anthropic_api_key.get_secret_value() or None,
            max_attempts=settings.llm_max_attempts,
            effort=settings.llm_effort or None,
        )
    msg = f"unsupported VME_LLM_PROVIDER={settings.llm_provider!r} (known: {ANTHROPIC})"
    raise LlmError(msg)
