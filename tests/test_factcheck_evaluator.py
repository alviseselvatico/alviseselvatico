from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import NOW, make_policy, make_source, requires_ffmpeg
from tests.fake_llm import FakeLlm
from tests.fake_stt import FakeSpeechToText
from vme.domain.models import (
    BasisType,
    Claim,
    ClaimImportance,
    ClaimStatus,
    ClaimType,
    DraftStatus,
    LlmCall,
    LlmValidationStatus,
    new_id,
)
from vme.editorial.service import generate_editorial
from vme.factcheck.evaluator import EvaluationPolicy, evaluate_claim
from vme.factcheck.evidence import AnthropicWebSearchRetriever, EvidenceItem, RetrievalResult
from vme.ingestion.register import register_local_media
from vme.reporting.cost import build_cost_report, load_prices
from vme.segmentation.segmenter import SegmentationConfig
from vme.segmentation.service import segment_and_store
from vme.storage.db import Store
from vme.transcription.service import transcribe_media

SENTENCES = [f"Sentence {i} about markets with a personal view on revenue." for i in range(6)]


class FakeRetriever:
    def __init__(
        self, items: list[EvidenceItem] | None = None, *, fail: bool = False, searches: int = 2
    ) -> None:
        self.items = (
            items
            if items is not None
            else [
                EvidenceItem(
                    "https://example.org/a", "Source A", "the claim is stated here", "2024-01-01"
                ),
                EvidenceItem("https://example.org/b", "Source B", None, None),
            ]
        )
        self.fail = fail
        self.searches = searches
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return "fake:search"

    def retrieve(self, claim_text: str, context: str) -> RetrievalResult:
        self.calls.append(claim_text)
        call = LlmCall(
            id=new_id("llm"),
            purpose="evidence_retrieval",
            prompt_name="evidence_retrieval",
            prompt_version="1.0.0",
            provider="fake",
            model_alias="strong",
            model_id_reported="claude-opus-5",
            parameters={"web_search_requests": self.searches},
            validation_status=LlmValidationStatus.FAILED
            if self.fail
            else LlmValidationStatus.VALID,
            attempts=1,
            latency_ms=5,
            input_tokens=100,
            output_tokens=50,
            error="APIConnectionError: boom" if self.fail else None,
            created_at=NOW,
        )
        return RetrievalResult(
            "fake:search", [] if self.fail else self.items, "summary", call, self.searches
        )


def _draft_with_claims(
    store: Store, audio: Path, artifacts: Path, claims: list[tuple[str, ClaimImportance]]
) -> tuple[str, list[Claim]]:
    store.sources.add(make_source())
    policy = make_policy(BasisType.OWNED)
    store.policies.add(policy)
    store.sources.attach_policy("S001", policy.id)
    asset = register_local_media(store, "S001", audio, artifacts_dir=artifacts, now=NOW)
    t = transcribe_media(
        store, asset.id, FakeSpeechToText(SENTENCES, pause_ms=200), artifacts_dir=artifacts, now=NOW
    )
    cands = segment_and_store(
        store, t.id, SegmentationConfig(min_ms=4000, target_ms=8000, max_ms=12000), now=NOW
    )
    draft = generate_editorial(
        store, cands[0].id, FakeLlm(editorial_claims=0, extractor_claims=0), now=NOW
    ).draft
    rows = [
        Claim(
            id=f"clm_{i}",
            editorial_version_id=draft.id,
            claim_text=text,
            claim_type=ClaimType.FACT,
            importance=imp,
            origin="test",
            created_at=NOW,
        )
        for i, (text, imp) in enumerate(claims)
    ]
    with store.transaction():
        store.claims.add_many(rows)
        store.editorial.set_status(draft.id, DraftStatus.BLOCKED_FACTCHECK)
    return draft.id, rows


@requires_ffmpeg
def test_supported_medium_claim_is_resolved_and_draft_unblocks(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    draft_id, (claim,) = _draft_with_claims(
        store, audio_wav, artifacts_dir, [("[supported] said in 1961", ClaimImportance.MEDIUM)]
    )
    retriever = FakeRetriever()
    ev = evaluate_claim(store, claim.id, retriever=retriever, llm=FakeLlm(), now=NOW)
    assert ev.applied_status is ClaimStatus.SUPPORTED and ev.verdict is not None
    assert ev.verdict.status.value == "SUPPORTED" and ev.verdict.supporting_evidence_ids == [
        "E1",
        "E2",
    ]
    stored = store.claims.get(claim.id)
    assert stored.status is ClaimStatus.SUPPORTED and stored.machine_status == "SUPPORTED"
    assert stored.evaluated_at == NOW and stored.machine_reason
    assert [e.url for e in store.evidence.list(claim.id)] == [
        "https://example.org/a",
        "https://example.org/b",
    ]
    purposes = {c.purpose for c in store.llm_calls.list()}
    assert {"evidence_retrieval", "factcheck_evaluation"} <= purposes
    events = store.reviews.list("claim", claim.id)
    assert events[0].decision == "machine_supported" and events[0].reviewer.startswith(
        "factcheck_evaluator:"
    )
    assert (
        store.editorial.get(draft_id).status is DraftStatus.NEEDS_REVIEW
    )  # gate cleared automatically
    # second run skips a resolved claim without spending
    again = evaluate_claim(store, claim.id, retriever=retriever, llm=FakeLlm(), now=NOW)
    assert (
        again.verdict is None
        and "skipped" in again.note
        and retriever.calls == ["[supported] said in 1961"]
    )


@requires_ffmpeg
def test_high_importance_supported_still_needs_human(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    draft_id, (claim,) = _draft_with_claims(
        store, audio_wav, artifacts_dir, [("[supported] big number", ClaimImportance.HIGH)]
    )
    ev = evaluate_claim(store, claim.id, retriever=FakeRetriever(), llm=FakeLlm(), now=NOW)
    assert ev.applied_status is ClaimStatus.UNVERIFIED and "human confirmation" in ev.note
    stored = store.claims.get(claim.id)
    assert stored.machine_status == "SUPPORTED" and stored.status is ClaimStatus.UNVERIFIED
    assert stored.reviewer_note and "D030" in stored.reviewer_note
    assert store.editorial.get(draft_id).status is DraftStatus.BLOCKED_FACTCHECK
    # the policy is explicit configuration: an empty set lets the machine clear HIGH claims
    relaxed = EvaluationPolicy(human_confirmation_for=frozenset())
    with store.transaction():
        store.claims.set_status(claim.id, ClaimStatus.UNVERIFIED, None)
    ev2 = evaluate_claim(
        store, claim.id, retriever=FakeRetriever(), llm=FakeLlm(), policy=relaxed, now=NOW
    )
    assert ev2.applied_status is ClaimStatus.SUPPORTED


@requires_ffmpeg
def test_contradicted_ambiguous_and_insufficient(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    draft_id, claims = _draft_with_claims(
        store,
        audio_wav,
        artifacts_dir,
        [
            ("[contradicted] wrong year", ClaimImportance.LOW),
            ("[ambiguous] depends", ClaimImportance.LOW),
            ("nothing known", ClaimImportance.LOW),
        ],
    )
    results = [
        evaluate_claim(store, c.id, retriever=FakeRetriever(), llm=FakeLlm(), now=NOW)
        for c in claims
    ]
    assert [r.applied_status for r in results] == [
        ClaimStatus.CONTRADICTED,
        ClaimStatus.AMBIGUOUS,
        ClaimStatus.UNVERIFIED,
    ]
    assert results[1].verdict is not None and results[1].verdict.qualification_needed
    assert store.claims.get(claims[2].id).machine_status == "INSUFFICIENT"
    assert store.editorial.get(draft_id).status is DraftStatus.BLOCKED_FACTCHECK


@requires_ffmpeg
def test_supported_without_valid_evidence_ids_downgrades_to_insufficient(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    _, (claim,) = _draft_with_claims(
        store, audio_wav, artifacts_dir, [("[supported] but no evidence", ClaimImportance.LOW)]
    )
    ev = evaluate_claim(store, claim.id, retriever=FakeRetriever(items=[]), llm=FakeLlm(), now=NOW)
    assert (
        ev.applied_status is ClaimStatus.UNVERIFIED
        and store.claims.get(claim.id).machine_status == "INSUFFICIENT"
    )


@requires_ffmpeg
def test_retrieval_failure_is_recorded_and_claim_untouched(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    _, (claim,) = _draft_with_claims(
        store, audio_wav, artifacts_dir, [("[supported] x", ClaimImportance.LOW)]
    )
    ev = evaluate_claim(store, claim.id, retriever=FakeRetriever(fail=True), llm=FakeLlm(), now=NOW)
    assert ev.verdict is None and "retrieval failed" in ev.note
    assert (
        store.claims.get(claim.id).status is ClaimStatus.UNVERIFIED
        and store.claims.get(claim.id).machine_status is None
    )
    retrievals = [c for c in store.llm_calls.list() if c.purpose == "evidence_retrieval"]
    assert retrievals and retrievals[-1].validation_status is LlmValidationStatus.FAILED


@requires_ffmpeg
def test_web_searches_are_priced_in_cost_report(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    _, (claim,) = _draft_with_claims(
        store, audio_wav, artifacts_dir, [("[supported] x", ClaimImportance.LOW)]
    )
    evaluate_claim(store, claim.id, retriever=FakeRetriever(searches=4), llm=FakeLlm(), now=NOW)
    report = build_cost_report(store, load_prices())
    assert report.web_searches == 4
    retrieval = next(s for s in report.stages if s.purpose == "evidence_retrieval")
    assert retrieval.cost_usd is not None and retrieval.cost_usd >= 4 * 10.0 / 1000


# ------------------------------------------------------------ retriever parsing


@dataclass
class _Cit:
    url: str
    title: str | None = None
    cited_text: str | None = None


@dataclass
class _Text:
    text: str
    citations: list[Any] = field(default_factory=list)
    type: str = "text"


@dataclass
class _Res:
    url: str
    title: str | None = None
    page_age: str | None = None
    type: str = "web_search_result"


@dataclass
class _SearchResult:
    content: Any
    type: str = "web_search_tool_result"


@dataclass
class _STU:
    web_search_requests: int = 2


@dataclass
class _Usage:
    input_tokens: int = 500
    output_tokens: int = 200
    server_tool_use: Any = field(default_factory=_STU)


@dataclass
class _Response:
    content: list[Any]
    stop_reason: str = "end_turn"
    model: str = "claude-opus-5"
    usage: _Usage = field(default_factory=_Usage)


class _Messages:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.kwargs: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.kwargs.append(kwargs)
        return self.responses.pop(0)


class _Client:
    def __init__(self, responses: list[Any]) -> None:
        self.messages = _Messages(responses)


def test_retriever_merges_search_results_and_citations() -> None:
    client = _Client(
        [
            _Response(
                [
                    _SearchResult(
                        [
                            _Res("https://a.org/x", "A title", "2 years ago"),
                            _Res("https://b.org/y", "B title"),
                        ]
                    ),
                    _Text(
                        "The speech was given in 1961.",
                        [_Cit("https://a.org/x", "A title", "delivered on January 20, 1961")],
                    ),
                    _Text("Nothing on B."),
                ]
            )
        ]
    )
    r = AnthropicWebSearchRetriever(models={"strong": "m"}, client=client, max_searches=2)
    out = r.retrieve("claim", "ctx")
    assert out.retriever == "anthropic:web_search_20260209" and out.searches == 2
    assert [i.url for i in out.items] == ["https://a.org/x", "https://b.org/y"]
    assert (
        out.items[0].snippet == "delivered on January 20, 1961"
        and out.items[0].published == "2 years ago"
    )
    assert out.items[1].snippet is None
    assert "speech was given" in out.summary
    assert out.call is not None and out.call.parameters["web_search_requests"] == 2
    assert (
        out.call.validation_status is LlmValidationStatus.VALID
        and out.call.model_id_reported == "claude-opus-5"
    )
    sent = client.messages.kwargs[0]
    assert sent["tools"] == [{"type": "web_search_20260209", "name": "web_search", "max_uses": 2}]
    assert sent["model"] == "m" and "output_format" not in sent


def test_retriever_continues_on_pause_turn_and_reports_errors() -> None:
    first = _Response([_Text("searching...")], stop_reason="pause_turn")
    second = _Response(
        [_SearchResult(type("Err", (), {"error_code": "max_uses_exceeded"})()), _Text("done")]
    )
    client = _Client([first, second])
    r = AnthropicWebSearchRetriever(models={"strong": "m"}, client=client)
    out = r.retrieve("claim", "ctx")
    assert len(client.messages.kwargs) == 2 and out.items == [] and out.searches == 4
    assert client.messages.kwargs[1]["messages"][1]["role"] == "assistant"
    with pytest.raises(Exception, match="VME_LLM_MODEL_STRONG"):
        AnthropicWebSearchRetriever(models={"strong": ""}, client=client).retrieve("c", "x")
