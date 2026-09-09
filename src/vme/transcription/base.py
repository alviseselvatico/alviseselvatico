"""Provider-agnostic speech-to-text contract (ARCHITECTURE §5, D005, D011)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from vme.domain.models import TranscriptSegment


class TranscriptionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    provider: str
    provider_version: str
    model_alias: str
    language: str | None
    language_probability: float | None
    duration_ms: int | None
    segments: list[TranscriptSegment]
    parameters: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())


class SpeechToText(Protocol):
    """One media file in, timestamped segments out. Implementations own their SDK."""

    @property
    def provider(self) -> str: ...

    @property
    def model_alias(self) -> str: ...

    def transcribe(self, path: Path) -> TranscriptionResult: ...
