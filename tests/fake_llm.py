"""Deterministic in-memory StructuredLlm for tests (no SDK, no network)."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel

from vme.domain.models import LlmValidationStatus
from vme.editorial.schemas import ClaimExtraction, EditorialDraft
from vme.factcheck.evaluator import FactCheckVerdict
from vme.llm.base import LlmOutcome, LlmRequest
from vme.ranking.schemas import COMPONENTS, LLM_PENALTIES, CandidateScore


def _unit(seed: str, salt: str) -> float:
    digest = hashlib.sha256(f"{seed}:{salt}".encode()).digest()
    return round(digest[0] / 255.0, 3)


def score_for(text: str, *, boost: float = 0.0) -> CandidateScore:
    """Deterministic score derived from the excerpt text."""
    data: dict[str, Any] = {
        name: min(1.0, max(0.0, _unit(text, name) + boost)) for name in COMPONENTS
    }
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
        editorial_claims: int = 1,
        extractor_claims: int = 1,
        fail_prompt: str | None = None,
    ) -> None:
        self.aliases = set(aliases)
        self.fail_alias = fail_alias
        self.strong_boost = strong_boost
        self.editorial_claims = editorial_claims
        self.extractor_claims = extractor_claims
        self.fail_prompt = fail_prompt
        self.calls: list[tuple[str, LlmRequest]] = []

    @property
    def provider(self) -> str:
        return "fake"

    def has_alias(self, alias: str) -> bool:
        return alias in self.aliases

    def generate[T: BaseModel](
        self, alias: str, request: LlmRequest, schema: type[T]
    ) -> LlmOutcome[T]:
        self.calls.append((alias, request))
        if alias == self.fail_alias or request.prompt_name == self.fail_prompt:
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
        parsed: Any
        if schema is CandidateScore:
            excerpt = request.user.split("EXCERPT:\n", 1)[1].split("\n\nContext after", 1)[0]
            parsed = score_for(excerpt, boost=self.strong_boost if alias == "strong" else 0.0)
        elif schema is EditorialDraft:
            rng = request.user.split("Excerpt range: ", 1)[1].split("\n", 1)[0]
            start, end = (int(x.split()[0]) for x in rng.split(" - "))
            parsed = EditorialDraft.model_validate(
                {
                    "hook": "Why this moment matters more than it sounds",
                    "commentary_before": (
                        "The speaker frames a personal view here, not a company forecast."
                    ),
                    "source_excerpt_plan": [
                        {"start_ms": start, "end_ms": end, "purpose": "core statement"}
                    ],
                    "commentary_after": "Context: this was said before the 2026 results were out.",
                    "title_options": ["The line everyone missed", "A personal view, not guidance"],
                    "cta": None,
                    "generated_claims": [
                        {
                            "text": f"This was said before the 2026 results were out ({i})",
                            "type": "FACT",
                            "importance": "HIGH",
                            "verification_required": True,
                        }
                        for i in range(self.editorial_claims)
                    ],
                    "transformation_summary": (
                        "Added timing context and reframed the opinion as opinion."
                    ),
                }
            )
        elif schema is ClaimExtraction:
            parsed = ClaimExtraction.model_validate(
                {
                    "claims": [
                        {
                            "text": "This was said before the 2026 results were out (0)"
                            if i == 0
                            else f"Extractor-only claim {i}",
                            "type": "FACT",
                            "importance": "MEDIUM",
                            "verification_required": True,
                        }
                        for i in range(self.extractor_claims)
                    ]
                }
            )
        elif schema is FactCheckVerdict:
            claim = request.user.split("CLAIM (", 1)[1].split("\n", 2)[1].lower()
            n_items = request.user.count("[E")
            ids = [f"E{i}" for i in range(1, n_items + 1)]
            if "[supported]" in claim:
                data = {
                    "status": "SUPPORTED",
                    "reason": "matches evidence",
                    "supporting_evidence_ids": ids,
                    "qualification_needed": None,
                }
            elif "[contradicted]" in claim:
                data = {
                    "status": "CONTRADICTED",
                    "reason": "evidence says otherwise",
                    "supporting_evidence_ids": ids[:1],
                    "qualification_needed": None,
                }
            elif "[ambiguous]" in claim:
                data = {
                    "status": "AMBIGUOUS",
                    "reason": "depends",
                    "supporting_evidence_ids": [],
                    "qualification_needed": "only after 1933",
                }
            else:
                data = {
                    "status": "INSUFFICIENT",
                    "reason": "nothing bears on it",
                    "supporting_evidence_ids": [],
                    "qualification_needed": None,
                }
            parsed = FactCheckVerdict.model_validate(data)
        else:  # pragma: no cover
            raise AssertionError(f"unexpected schema {schema}")
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
