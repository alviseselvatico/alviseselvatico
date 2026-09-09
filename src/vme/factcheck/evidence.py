"""Evidence retrieval behind a small interface (D005). First adapter: Anthropic web search.

The retrieval call is recorded as an ``LlmCall`` (purpose ``evidence_retrieval``) with the
number of web searches in its parameters, so the cost report can price them.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from vme.domain.models import LlmCall, LlmValidationStatus, new_id, utc_now
from vme.logs import get_logger

log = get_logger("factcheck.evidence")

PROVIDER = "anthropic"
RETRIEVAL_PROMPT_NAME = "evidence_retrieval"
RETRIEVAL_PROMPT_VERSION = "1.0.0"
_MAX_TURNS = 3

RETRIEVAL_SYSTEM = """You are a fact-checking researcher. For the claim you are given, search the
web for primary or authoritative sources that confirm, refute or qualify it. Prefer primary
material (official records, the original speech or document, reputable reference works)
over commentary. Quote the passages that matter. If you find nothing reliable, say so
plainly. Do not decide the verdict; report the evidence."""


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    url: str
    title: str | None
    snippet: str | None
    published: str | None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    retriever: str
    items: list[EvidenceItem]
    summary: str
    call: LlmCall | None = None
    searches: int = 0
    parameters: dict[str, Any] = field(default_factory=dict)


class EvidenceRetriever(Protocol):
    @property
    def name(self) -> str: ...

    def retrieve(self, claim_text: str, context: str) -> RetrievalResult: ...


class RetrievalError(RuntimeError):
    pass


class AnthropicWebSearchRetriever:
    def __init__(
        self,
        *,
        models: Mapping[str, str],
        alias: str = "strong",
        api_key: str | None = None,
        search_tool_type: str = "web_search_20260209",
        max_searches: int = 3,
        max_tokens: int = 4096,
        max_items: int = 8,
        client: Any | None = None,
    ) -> None:
        self._models = {a: m for a, m in models.items() if m}
        self._alias = alias
        self._api_key = api_key or None
        self._tool_type = search_tool_type
        self._max_searches = max(1, max_searches)
        self._max_tokens = max_tokens
        self._max_items = max_items
        self._client = client

    @property
    def name(self) -> str:
        return f"{PROVIDER}:{self._tool_type}"

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def retrieve(self, claim_text: str, context: str) -> RetrievalResult:
        model_id = self._models.get(self._alias)
        if not model_id:
            msg = (
                f"no model configured for alias {self._alias!r} "
                f"(VME_LLM_MODEL_{self._alias.upper()})"
            )
            raise RetrievalError(msg)
        import anthropic

        client = self._get_client()
        user = (
            f"CLAIM TO CHECK:\n{claim_text}\n\n"
            f"CONTEXT (where the claim appears):\n{context}\n\n"
            "Search for evidence, quote the decisive passages with their sources, and end with "
            "a two-sentence summary of what the evidence shows."
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        tools = [{"type": self._tool_type, "name": "web_search", "max_uses": self._max_searches}]
        parameters = {
            "model_id": model_id,
            "search_tool": self._tool_type,
            "max_uses": self._max_searches,
            "max_tokens": self._max_tokens,
        }
        started = time.perf_counter()
        items: dict[str, EvidenceItem] = {}
        texts: list[str] = []
        in_tok = out_tok = 0
        searches = 0
        model_reported: str | None = None
        error: str | None = None
        try:
            for _ in range(_MAX_TURNS):
                response = client.messages.create(
                    model=model_id,
                    max_tokens=self._max_tokens,
                    system=RETRIEVAL_SYSTEM,
                    messages=messages,
                    tools=tools,
                )
                model_reported = getattr(response, "model", model_reported)
                usage = getattr(response, "usage", None)
                in_tok += getattr(usage, "input_tokens", 0) or 0
                out_tok += getattr(usage, "output_tokens", 0) or 0
                stu = getattr(usage, "server_tool_use", None)
                searches += getattr(stu, "web_search_requests", 0) or 0
                for block in response.content:
                    self._collect(block, items, texts)
                if response.stop_reason == "refusal":
                    error = "refusal"
                    break
                if response.stop_reason != "pause_turn":
                    break
                messages = [*messages, {"role": "assistant", "content": response.content}]
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        latency = round((time.perf_counter() - started) * 1000)
        summary = "\n".join(t for t in texts if t.strip()).strip()
        ordered = list(items.values())[: self._max_items]
        call = LlmCall(
            id=new_id("llm"),
            purpose="evidence_retrieval",
            input_artifact_refs=[],
            prompt_name=RETRIEVAL_PROMPT_NAME,
            prompt_version=RETRIEVAL_PROMPT_VERSION,
            provider=PROVIDER,
            model_alias=self._alias,
            model_id_reported=model_reported,
            parameters={**parameters, "web_search_requests": searches},
            response={
                "summary": summary,
                "items": [
                    {"url": i.url, "title": i.title, "snippet": i.snippet, "published": i.published}
                    for i in ordered
                ],
            },
            validation_status=LlmValidationStatus.FAILED if error else LlmValidationStatus.VALID,
            attempts=1,
            latency_ms=latency,
            input_tokens=in_tok,
            output_tokens=out_tok,
            error=error,
            created_at=utc_now(),
        )
        if error:
            log.error("evidence_retrieval_failed", extra={"error": error, "searches": searches})
        else:
            log.info(
                "evidence_retrieved",
                extra={"items": len(ordered), "searches": searches, "latency_ms": latency},
            )
        return RetrievalResult(
            retriever=self.name,
            items=ordered,
            summary=summary,
            call=call,
            searches=searches,
            parameters=parameters,
        )

    @staticmethod
    def _collect(block: Any, items: dict[str, EvidenceItem], texts: list[str]) -> None:
        btype = getattr(block, "type", None)
        if btype == "text":
            texts.append(getattr(block, "text", "") or "")
            for cit in getattr(block, "citations", None) or []:
                url = getattr(cit, "url", None)
                if url:
                    prev = items.get(url)
                    snippet = getattr(cit, "cited_text", None) or (prev.snippet if prev else None)
                    items[url] = EvidenceItem(
                        url=url,
                        title=getattr(cit, "title", None) or (prev.title if prev else None),
                        snippet=snippet,
                        published=prev.published if prev else None,
                    )
        elif btype == "web_search_tool_result":
            content = getattr(block, "content", None)
            if isinstance(content, list):
                for r in content:
                    url = getattr(r, "url", None)
                    if not url:
                        continue
                    prev = items.get(url)
                    items[url] = EvidenceItem(
                        url=url,
                        title=getattr(r, "title", None) or (prev.title if prev else None),
                        snippet=prev.snippet if prev else None,
                        published=getattr(r, "page_age", None)
                        or (prev.published if prev else None),
                    )
            else:  # error object: report, never pretend evidence exists
                code = getattr(content, "error_code", None)
                log.warning("web_search_error", extra={"error_code": code})
