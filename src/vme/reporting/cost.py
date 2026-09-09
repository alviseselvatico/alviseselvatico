"""Cost and throughput report over stored ``LlmCall`` rows (DATA_MODEL §6).

Prices are configuration, never code (D021): token counts are recorded per call at run
time and a versioned price list is applied here, so a price change re-prices history
instead of invalidating it. Unpriced models are reported, not silently costed at zero.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from vme.domain.models import DraftStatus, LlmCall, LlmValidationStatus
from vme.storage.db import Store

DEFAULT_PRICE_RESOURCE = "anthropic_v1.json"
_PER_MILLION = 1_000_000


class ModelPrice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input: float = Field(ge=0)
    output: float = Field(ge=0)


class PriceList(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    currency: str = "USD"
    source: str = ""
    per_million_tokens: dict[str, ModelPrice]

    def resolve(self, model_id: str | None) -> ModelPrice | None:
        """Match an exact id, or the base model of a dated snapshot.

        Providers report the snapshot they served (``claude-haiku-4-5-20251001``) while
        configuration names the alias (``claude-haiku-4-5``). Only a ``-`` separated
        suffix is accepted, so one model never absorbs another's price.
        """
        if not model_id:
            return None
        exact = self.per_million_tokens.get(model_id)
        if exact is not None:
            return exact
        matches = [
            (base, price)
            for base, price in self.per_million_tokens.items()
            if model_id.startswith(f"{base}-")
        ]
        if not matches:
            return None
        return max(matches, key=lambda kv: len(kv[0]))[1]

    def cost(self, model_id: str | None, input_tokens: int, output_tokens: int) -> float | None:
        price = self.resolve(model_id)
        if price is None:
            return None
        return (input_tokens * price.input + output_tokens * price.output) / _PER_MILLION


def load_prices(path: Path | None = None) -> PriceList:
    if path is None:
        text = (
            resources.files("vme.reporting.pricing")
            .joinpath(DEFAULT_PRICE_RESOURCE)
            .read_text(encoding="utf-8")
        )
    else:
        text = path.read_text(encoding="utf-8")
    return PriceList.model_validate(json.loads(text))


@dataclass(frozen=True, slots=True)
class StageCost:
    purpose: str
    model_alias: str
    model_id: str | None
    calls: int
    failed_calls: int
    retried_calls: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float | None
    unmetered_attempts: int = 0

    @property
    def avg_latency_ms(self) -> int:
        return round(self.latency_ms / self.calls) if self.calls else 0


@dataclass(frozen=True, slots=True)
class CostReport:
    prices_version: str
    currency: str
    stages: list[StageCost]
    total_cost_usd: float
    unpriced_models: list[str]
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def unmetered_attempts(self) -> int:
        """Provider attempts whose token usage the SDK never surfaced.

        A response rejected by schema validation raises before the usage block reaches
        us, so its tokens are billed by the provider but absent here: with any of these,
        ``total_cost_usd`` is a lower bound, not the invoice.
        """
        return sum(s.unmetered_attempts for s in self.stages)

    @property
    def is_lower_bound(self) -> bool:
        return self.unmetered_attempts > 0 or bool(self.unpriced_models)

    @property
    def total_calls(self) -> int:
        return sum(s.calls for s in self.stages)

    @property
    def total_input_tokens(self) -> int:
        return sum(s.input_tokens for s in self.stages)

    @property
    def total_output_tokens(self) -> int:
        return sum(s.output_tokens for s in self.stages)

    @property
    def wasted_cost_usd(self) -> float:
        """Spend on calls that produced nothing usable."""
        return round(
            sum(
                (s.cost_usd or 0.0) * (s.failed_calls / s.calls)
                for s in self.stages
                if s.calls and s.failed_calls
            ),
            6,
        )

    def per_unit(self, unit: str) -> float | None:
        n = self.counts.get(unit, 0)
        return round(self.total_cost_usd / n, 4) if n else None


def build_cost_report(store: Store, prices: PriceList) -> CostReport:
    calls = store.llm_calls.list()
    buckets: dict[tuple[str, str, str | None], list[LlmCall]] = {}
    for call in calls:
        buckets.setdefault((call.purpose, call.model_alias, call.model_id_reported), []).append(
            call
        )

    stages: list[StageCost] = []
    unpriced: set[str] = set()
    total = 0.0
    for (purpose, alias, model_id), group in sorted(buckets.items(), key=lambda kv: kv[0][:2]):
        in_tok = sum(c.input_tokens or 0 for c in group)
        out_tok = sum(c.output_tokens or 0 for c in group)
        cost = prices.cost(model_id, in_tok, out_tok)
        if cost is None and (in_tok or out_tok):
            unpriced.add(model_id or "(not reported)")
        total += cost or 0.0
        stages.append(
            StageCost(
                purpose=purpose,
                model_alias=alias,
                model_id=model_id,
                calls=len(group),
                failed_calls=sum(c.validation_status is LlmValidationStatus.FAILED for c in group),
                retried_calls=sum(c.attempts > 1 for c in group),
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_ms=sum(c.latency_ms for c in group),
                cost_usd=round(cost, 6) if cost is not None else None,
                unmetered_attempts=sum(max(0, c.attempts - 1) for c in group)
                + sum(c.validation_status is LlmValidationStatus.FAILED for c in group),
            )
        )

    drafts = store.editorial.list()
    counts = {
        "media_assets": len(store.media.list()),
        "transcripts": len(store.transcripts.list()),
        "candidates": sum(len(store.candidates.list(t.id)) for t in store.transcripts.list()),
        "drafts": len(drafts),
        "approved_drafts": sum(d.status is DraftStatus.APPROVED for d in drafts),
        "renders": len(store.renders.list_renders()),
    }
    return CostReport(
        prices_version=prices.version,
        currency=prices.currency,
        stages=stages,
        total_cost_usd=round(total, 6),
        unpriced_models=sorted(unpriced),
        counts=counts,
    )
