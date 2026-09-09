"""Deterministic boundary editor: align a span to sentence boundaries and drop boilerplate.

A first pass before any LLM boundary editing (PROMPT_CONTRACTS §5): it fixes the defects
that word timestamps and punctuation already make visible - a span that starts or ends
mid-sentence, a LibriVox/announcer boilerplate sentence at an edge, a "Part four ..." or
"Chapter 2 ..." marker - and never touches the interior of an excerpt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from vme.domain.models import Word

BOUNDARY_EDITOR_VERSION = "v0.1.0"
_TERMINAL = (".", "?", "!")

BOILERPLATE = re.compile(
    r"(libri?vox|this is a libra?vox recording|for more information or to volunteer|"
    r"recording by [a-z]|read (in english )?by|end of (section|chapter|part|book)|"
    r"^(section|chapter|book) \d+\b|public domain|has spoken to you from|"
    r"ladies and gentlemen,? the president)",
    re.IGNORECASE,
)
SECTION_MARKER = re.compile(
    r"^(part|section|chapter)\s+(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class BoundaryConfig:
    version: str = BOUNDARY_EDITOR_VERSION
    min_ms: int = 8_000
    max_ms: int = 90_000
    max_extend_ms: int = 6_000
    allow_extend: bool = True
    drop_boilerplate: bool = True


@dataclass(frozen=True, slots=True)
class BoundaryEdit:
    start_ms: int
    end_ms: int
    reasons: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.reasons)


def _is_sentence_start(words: list[Word], i: int) -> bool:
    return i == 0 or words[i - 1].text.endswith(_TERMINAL)


def _is_sentence_end(words: list[Word], i: int) -> bool:
    return i == len(words) - 1 or words[i].text.endswith(_TERMINAL)


def _prev_sentence_start(words: list[Word], i: int) -> int:
    j = i
    while j > 0 and not _is_sentence_start(words, j):
        j -= 1
    return j


def _next_sentence_start(words: list[Word], i: int) -> int | None:
    j = i + 1
    while j < len(words) and not _is_sentence_start(words, j):
        j += 1
    return j if j < len(words) else None


def _next_sentence_end(words: list[Word], i: int) -> int:
    j = i
    while j < len(words) - 1 and not _is_sentence_end(words, j):
        j += 1
    return j


def _prev_sentence_end(words: list[Word], i: int) -> int | None:
    j = i - 1
    while j >= 0 and not _is_sentence_end(words, j):
        j -= 1
    return j if j >= 0 else None


def _sentence_text(words: list[Word], start: int) -> str:
    end = _next_sentence_end(words, start)
    return " ".join(w.text for w in words[start : end + 1])


def _is_noise(text: str) -> bool:
    return bool(BOILERPLATE.search(text) or SECTION_MARKER.match(text.strip()))


def refine_span(
    words: list[Word], start_ms: int, end_ms: int, config: BoundaryConfig | None = None
) -> BoundaryEdit:
    """Return the adjusted span. Pure: same words and span always give the same edit."""
    config = config or BoundaryConfig()
    inside = [i for i, w in enumerate(words) if w.start_ms >= start_ms and w.end_ms <= end_ms]
    if not inside:
        return BoundaryEdit(start_ms, end_ms)
    first, last = inside[0], inside[-1]
    reasons: list[str] = []

    def duration(a: int, b: int) -> int:
        return words[b].end_ms - words[a].start_ms

    # 1. start mid-sentence: extend back if cheap, else trim the fragment.
    if not _is_sentence_start(words, first):
        back = _prev_sentence_start(words, first)
        extension = words[first].start_ms - words[back].start_ms
        if (
            config.allow_extend
            and extension <= config.max_extend_ms
            and duration(back, last) <= config.max_ms
        ):
            first = back
            reasons.append("extended_start")
        else:
            nxt = _next_sentence_start(words, first)
            if nxt is not None and nxt <= last and duration(nxt, last) >= config.min_ms:
                first = nxt
                reasons.append("trimmed_start_fragment")

    # 2. end mid-sentence: extend forward if cheap, else cut back to the last full sentence.
    if not _is_sentence_end(words, last):
        fwd = _next_sentence_end(words, last)
        extension = words[fwd].end_ms - words[last].end_ms
        if (
            config.allow_extend
            and extension <= config.max_extend_ms
            and duration(first, fwd) <= config.max_ms
        ):
            last = fwd
            reasons.append("extended_end")
        else:
            prev = _prev_sentence_end(words, last + 1)
            if prev is not None and prev >= first and duration(first, prev) >= config.min_ms:
                last = prev
                reasons.append("trimmed_end_fragment")

    # 3. boilerplate or section markers at either edge.
    if config.drop_boilerplate:
        while first < last and _is_noise(_sentence_text(words, first)):
            nxt = _next_sentence_start(words, first)
            if nxt is None or nxt > last or duration(nxt, last) < config.min_ms:
                break
            first = nxt
            reasons.append("dropped_boilerplate_start")
        while last > first:
            s = _prev_sentence_end(words, last)
            sent_start = s + 1 if s is not None else 0
            if sent_start < first or not _is_noise(_sentence_text(words, sent_start)):
                break
            if s is None or s < first or duration(first, s) < config.min_ms:
                break
            last = s
            reasons.append("dropped_boilerplate_end")

    return BoundaryEdit(words[first].start_ms, words[last].end_ms, reasons)
