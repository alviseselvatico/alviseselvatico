"""Deterministic in-memory StructuredLlm for tests (no SDK, no network)."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel

from vme.domain.models import LlmValidationStatus
from vme.llm.base import LlmOutcome, LlmRequest
from vme.ranking.schemas import COMPONENTS, LLM_PENALTIES, CandidateScore


def _unit(seed: str, salt: str) -> float:
    digest = hashlib.sha256(f"{seed}:{salt}".encode()).digest()
    return round(digest[0] / 255.0, 3)


def score_for(text: str, *, boost: float = 0.0) -> CandidateScore:
    """Deterministic score derived from the excerpt text."""
    data: dict[str, Any] = {name: min(1.0, _unit(text, name) + boost) for name in COMPONENTS}
    data.update({name: _unit(text, name) * 0.3 for name in LLM_PENALTIES})
    data.update(
        recommended_start_ms=0,
        recommended_end_ms=1000,
        why_it_might_work=f"work:{text[:20]}",
        why_it_might_fail=f"fail:{text[:20]}",
        key_claims=["the speaker says something checkable"],
    )
    return CandidateScore.model_validate(data)


class FakeLlm:
    def __init__(
        self,
        aliases: tuple[str, ...] = ("cheap", "strong"),
        *,
        fail_alias: str | None = None,
        strong_boost: float = 0.2,
    ) -> None:
        self.aliases = set(aliases)
        self.fail_alias = fail_alias
        self.strong_boost = strong_boost
        self.calls: list[tuple[str, LlmRequest]] = []

    @property
    def provider(self) -> str:
        return "fake"

    def has_alias(self, alias: str) -> bool:
        return alias in self.aliases

    def generate[T: BaseModel](
        self, alias: str, request: LlmRequest, schema: type[T]
    ) -> LlmOutcome[T]:
        assert schema is CandidateScore
        self.calls.append((alias, request))
        if alias == self.fail_alias:
            return LlmOutcome(
                provider="fake",
                model_alias=alias,
                model_id_reported="fake-model",
                parsed=None,
                response=None,
                validation_status=LlmValidationStatus.FAILED,
                attempts=2,
                latency_ms=1,
                input_tokens=10,
                output_tokens=0,
                parameters={"max_tokens": request.max_tokens},
                error="simulated failure",
            )
        excerpt = request.user.split("EXCERPT:\n", 1)[1].split("\n\nContext after", 1)[0]
        parsed: Any = score_for(excerpt, boost=self.strong_boost if alias == "strong" else 0.0)
        return LlmOutcome(
            provider="fake",
            model_alias=alias,
            model_id_reported="fake-model",
            parsed=parsed,
            response=parsed.model_dump(mode="json"),
            validation_status=LlmValidationStatus.VALID,
            attempts=1,
            latency_ms=1,
            input_tokens=100,
            output_tokens=50,
            parameters={"max_tokens": request.max_tokens},
        )
