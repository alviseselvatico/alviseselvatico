"""Segmenter v0: sentences from punctuation and pauses, windows from duration targets.

Deterministic by construction (PROJECT_BIBLE §6.4, §9: deterministic before LLM). It
produces *semantic-ish* spans, not fixed 30-second windows: boundaries only fall on
sentence ends, sentence ends come from the provider's punctuation or from pauses, and a
window closes at the target duration, at a hard pause once the minimum is reached, or
before it would exceed the maximum. Topic and speaker stay ``None`` in Phase 0 (no
diarization, no LLM at this stage). A change in behaviour bumps ``version``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from vme.domain.models import Candidate, Transcript, Word, new_id, utc_now

SEGMENTER_VERSION = "v0.1.0"
_SENTENCE_END = (".", "?", "!")


@dataclass(frozen=True, slots=True)
class SegmentationConfig:
    version: str = SEGMENTER_VERSION
    min_ms: int = 15_000
    target_ms: int = 35_000
    max_ms: int = 60_000
    sentence_pause_ms: int = 700
    hard_pause_ms: int = 1_500
    context_words: int = 40

    def __post_init__(self) -> None:
        if not (0 < self.min_ms <= self.target_ms <= self.max_ms):
            msg = "require 0 < min_ms <= target_ms <= max_ms"
            raise ValueError(msg)
        if self.sentence_pause_ms <= 0 or self.hard_pause_ms <= 0 or self.context_words < 0:
            msg = "pauses must be positive and context_words >= 0"
            raise ValueError(msg)


def created_by(config: SegmentationConfig) -> str:
    return f"segmenter:{config.version}"


@dataclass(frozen=True, slots=True)
class _Sentence:
    words: list[Word]

    @property
    def start_ms(self) -> int:
        return self.words[0].start_ms

    @property
    def end_ms(self) -> int:
        return self.words[-1].end_ms

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def _units(transcript: Transcript) -> list[Word]:
    """Words when the provider gave them; otherwise each segment acts as one unit."""
    words = transcript.words()
    if words:
        return words
    return [
        Word(text=s.text.strip(), start_ms=s.start_ms, end_ms=s.end_ms)
        for s in transcript.segments
        if s.text.strip()
    ]


def split_sentences(words: list[Word], config: SegmentationConfig) -> list[_Sentence]:
    sentences: list[_Sentence] = []
    current: list[Word] = []
    for i, w in enumerate(words):
        current.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        ends = w.text.endswith(_SENTENCE_END) or nxt is None
        pause = nxt is not None and (nxt.start_ms - w.end_ms) >= config.sentence_pause_ms
        if ends or pause:
            sentences.append(_Sentence(current))
            current = []
    return sentences


def _span(window: list[_Sentence]) -> int:
    return window[-1].end_ms - window[0].start_ms


def build_windows(sentences: list[_Sentence], config: SegmentationConfig) -> list[list[_Sentence]]:
    windows: list[list[_Sentence]] = []
    current: list[_Sentence] = []
    for i, s in enumerate(sentences):
        if current and (s.end_ms - current[0].start_ms) > config.max_ms:
            windows.append(current)
            current = []
        current.append(s)
        span = _span(current)
        nxt = sentences[i + 1] if i + 1 < len(sentences) else None
        gap = (nxt.start_ms - s.end_ms) if nxt is not None else 0
        if span >= config.target_ms or (span >= config.min_ms and gap >= config.hard_pause_ms):
            windows.append(current)
            current = []
    if current:
        # A short tail merges into the previous window when that stays within max_ms.
        if windows and _span(current) < config.min_ms:
            merged = windows[-1] + current
            if _span(merged) <= config.max_ms:
                windows[-1] = merged
                current = []
        if current:
            windows.append(current)
    return windows


def segment_transcript(
    transcript: Transcript,
    config: SegmentationConfig | None = None,
    *,
    now: datetime | None = None,
) -> list[Candidate]:
    """Pure function: same transcript + config => same candidates (ids aside)."""
    config = config or SegmentationConfig()
    now = now or utc_now()
    words = _units(transcript)
    if not words:
        return []
    sentences = split_sentences(words, config)
    windows = build_windows(sentences, config)
    candidates: list[Candidate] = []
    cursor = 0  # index into `words` of the first word of the current window
    for window in windows:
        n_words = sum(len(s.words) for s in window)
        before = words[max(0, cursor - config.context_words) : cursor]
        after = words[cursor + n_words : cursor + n_words + config.context_words]
        candidates.append(
            Candidate(
                id=new_id("cnd"),
                transcript_id=transcript.id,
                start_ms=window[0].start_ms,
                end_ms=max(window[-1].end_ms, window[0].start_ms + 1),
                context_before=" ".join(w.text for w in before),
                context_after=" ".join(w.text for w in after),
                speaker=None,
                topic=None,
                candidate_text=" ".join(s.text for s in window),
                created_by=created_by(config),
                created_at=now,
            )
        )
        cursor += n_words
    return candidates
