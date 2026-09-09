from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.conftest import NOW
from vme.domain.models import LlmCall, LlmValidationStatus
from vme.reporting.cost import build_cost_report, load_prices
from vme.storage.db import Store


def _call(
    alias: str,
    model_id: str | None,
    *,
    purpose: str = "candidate_scoring",
    in_tok: int = 1000,
    out_tok: int = 500,
    status: LlmValidationStatus = LlmValidationStatus.VALID,
    attempts: int = 1,
) -> LlmCall:
    return LlmCall(
        id=f"llm_{alias}_{model_id}_{attempts}_{status.value}_{in_tok}",
        purpose=purpose,
        prompt_name="p",
        prompt_version="1.0.0",
        provider="anthropic",
        model_alias=alias,
        model_id_reported=model_id,
        validation_status=status,
        attempts=attempts,
        latency_ms=1000,
        input_tokens=in_tok,
        output_tokens=out_tok,
        created_at=NOW,
    )


def test_default_price_list_is_valid() -> None:
    prices = load_prices()
    assert prices.currency == "USD" and prices.version.startswith("anthropic_")
    assert prices.per_million_tokens["claude-opus-5"].output == 25.0


def test_price_list_rejects_malformed_file(tmp_path: Path) -> None:
    bad = tmp_path / "p.json"
    bad.write_text(
        json.dumps({"version": "v", "per_million_tokens": {"m": {"input": -1, "output": 1}}})
    )
    with pytest.raises(ValidationError):
        load_prices(bad)


def test_dated_snapshot_resolves_to_its_base_price() -> None:
    prices = load_prices()
    exact = prices.cost("claude-haiku-4-5", 1_000_000, 0)
    snapshot = prices.cost("claude-haiku-4-5-20251001", 1_000_000, 0)
    assert exact == snapshot == 1.0
    # a bare unknown model stays unpriced, and one model never absorbs another's price
    assert prices.cost("some-other-model", 1_000_000, 0) is None
    assert prices.cost("claude-haiku-4-52", 1_000_000, 0) is None
    assert prices.cost(None, 1000, 1000) is None


def test_cost_report_totals_and_per_unit(store: Store) -> None:
    with store.transaction():
        store.llm_calls.add(
            _call("cheap", "claude-haiku-4-5-20251001", in_tok=1_000_000, out_tok=0)
        )
        store.llm_calls.add(_call("strong", "claude-opus-5", in_tok=1_000_000, out_tok=1_000_000))
    report = build_cost_report(store, load_prices())
    assert report.total_cost_usd == pytest.approx(1.0 + 5.0 + 25.0)
    assert report.unpriced_models == [] and not report.is_lower_bound
    assert report.total_calls == 2 and report.total_input_tokens == 2_000_000
    assert report.counts["approved_drafts"] == 0
    assert report.per_unit("approved_drafts") is None  # no division by zero


def test_unpriced_model_is_reported_not_silently_free(store: Store) -> None:
    with store.transaction():
        store.llm_calls.add(_call("strong", "mystery-model-9", in_tok=1_000_000, out_tok=0))
    report = build_cost_report(store, load_prices())
    assert report.total_cost_usd == 0.0
    assert report.unpriced_models == ["mystery-model-9"] and report.is_lower_bound


def test_retried_and_failed_attempts_make_the_total_a_lower_bound(store: Store) -> None:
    with store.transaction():
        store.llm_calls.add(
            _call("strong", "claude-opus-5", attempts=2, status=LlmValidationStatus.INVALID_RETRIED)
        )
        store.llm_calls.add(
            _call(
                "strong",
                "claude-opus-5",
                purpose="editorial_transform",
                in_tok=0,
                out_tok=0,
                attempts=2,
                status=LlmValidationStatus.FAILED,
            )
        )
    report = build_cost_report(store, load_prices())
    # 1 unmetered retry + (1 retry + 1 failed) = 3 provider attempts billed but not recorded
    assert report.unmetered_attempts == 3 and report.is_lower_bound
    assert report.total_cost_usd > 0  # the metered attempt still counts
