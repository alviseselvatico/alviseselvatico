"""Deterministic in-memory SpeechToText for unit tests (no model, no network)."""

from __future__ import annotations

from pathlib import Path

from vme.domain.models import TranscriptSegment, Word
from vme.transcription.base import TranscriptionResult

_SENTENCES = [
    "This is the first sentence of the talk.",
    "It sets up the topic and makes a claim.",
    "Now here is a surprising fact about the market!",
    "Do you really think revenue could double?",
    "Personally, I think it could.",
    "That is my opinion, not company guidance.",
    "Let us move to the second topic.",
    "It is about tooling and process.",
]


def make_words(
    sentences: list[str], *, word_ms: int = 300, gap_ms: int = 100, pause_ms: int = 0
) -> list[Word]:
    """Lay sentences on a timeline; ``pause_ms`` extra silence after each sentence."""
    words: list[Word] = []
    t = 0
    for sentence in sentences:
        for token in sentence.split():
            words.append(Word(text=token, start_ms=t, end_ms=t + word_ms, probability=0.9))
            t += word_ms + gap_ms
        t += pause_ms
    return words


class FakeSpeechToText:
    def __init__(self, sentences: list[str] | None = None, *, pause_ms: int = 0) -> None:
        self.sentences = sentences if sentences is not None else _SENTENCES
        self.pause_ms = pause_ms
        self.calls: list[Path] = []

    @property
    def provider(self) -> str:
        return "fake"

    @property
    def model_alias(self) -> str:
        return "fake-1"

    def transcribe(self, path: Path) -> TranscriptionResult:
        self.calls.append(path)
        words = make_words(self.sentences, pause_ms=self.pause_ms)
        segments: list[TranscriptSegment] = []
        i = 0
        for sentence in self.sentences:
            n = len(sentence.split())
            chunk = words[i : i + n]
            segments.append(
                TranscriptSegment(
                    start_ms=chunk[0].start_ms, end_ms=chunk[-1].end_ms, text=sentence, words=chunk
                )
            )
            i += n
        return TranscriptionResult(
            provider="fake",
            provider_version="0.0",
            model_alias="fake-1",
            language="en",
            language_probability=0.99,
            duration_ms=words[-1].end_ms if words else 0,
            segments=segments,
            parameters={"beam_size": 1},
        )
