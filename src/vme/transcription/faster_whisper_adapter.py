"""``faster-whisper`` adapter (D011): local, word-level timestamps, no diarization.

The SDK is imported lazily so the rest of the application (and its tests) never pays
for CTranslate2 or a model download. Model size, device and compute type come from
configuration; nothing here names a model beyond the size alias passed in.
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path
from typing import Any

from vme.domain.models import TranscriptSegment, Word
from vme.logs import get_logger
from vme.transcription.base import TranscriptionError, TranscriptionResult

log = get_logger("transcription.faster_whisper")

PROVIDER = "faster_whisper"


def _ms(seconds: float | None) -> int:
    return max(0, round((seconds or 0.0) * 1000))


class FasterWhisperTranscriber:
    def __init__(
        self,
        *,
        model_size: str,
        device: str = "cpu",
        compute_type: str = "int8",
        language: str | None = None,
        beam_size: int = 5,
    ) -> None:
        if not model_size:
            msg = "VME_STT_MODEL_SIZE is empty"
            raise TranscriptionError(msg)
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._language = language or None
        self._beam_size = beam_size
        self._model: Any = None

    @property
    def provider(self) -> str:
        return PROVIDER

    @property
    def model_alias(self) -> str:
        return self._model_size

    @property
    def provider_version(self) -> str:
        try:
            return metadata.version("faster-whisper")
        except metadata.PackageNotFoundError:
            return "unknown"

    def _load(self) -> Any:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:  # pragma: no cover - dependency is declared
                msg = "faster-whisper is not installed"
                raise TranscriptionError(msg) from exc
            log.info(
                "stt_model_loading",
                extra={
                    "provider": PROVIDER,
                    "model_alias": self._model_size,
                    "device": self._device,
                    "compute_type": self._compute_type,
                },
            )
            try:
                self._model = WhisperModel(
                    self._model_size, device=self._device, compute_type=self._compute_type
                )
            except Exception as exc:
                msg = f"could not load faster-whisper model {self._model_size!r}: {exc}"
                raise TranscriptionError(msg) from exc
        return self._model

    def transcribe(self, path: Path) -> TranscriptionResult:
        if not path.is_file():
            msg = f"media file not found: {path.name}"
            raise TranscriptionError(msg)
        model = self._load()
        params: dict[str, Any] = {
            "beam_size": self._beam_size,
            "word_timestamps": True,
            "language": self._language,
            "device": self._device,
            "compute_type": self._compute_type,
        }
        try:
            raw_segments, info = model.transcribe(
                str(path),
                beam_size=self._beam_size,
                word_timestamps=True,
                language=self._language,
            )
            segments: list[TranscriptSegment] = []
            for seg in raw_segments:  # generator: decoding happens here
                words = [
                    Word(
                        text=w.word.strip(),
                        start_ms=_ms(w.start),
                        end_ms=max(_ms(w.start), _ms(w.end)),
                        probability=float(w.probability) if w.probability is not None else None,
                    )
                    for w in (seg.words or [])
                    if w.word and w.word.strip()
                ]
                segments.append(
                    TranscriptSegment(
                        start_ms=_ms(seg.start),
                        end_ms=max(_ms(seg.start), _ms(seg.end)),
                        text=seg.text.strip(),
                        words=words,
                    )
                )
        except TranscriptionError:
            raise
        except Exception as exc:
            msg = f"faster-whisper failed on {path.name}: {exc}"
            raise TranscriptionError(msg) from exc
        return TranscriptionResult(
            provider=PROVIDER,
            provider_version=self.provider_version,
            model_alias=self._model_size,
            language=getattr(info, "language", None),
            language_probability=(
                float(info.language_probability)
                if getattr(info, "language_probability", None) is not None
                else None
            ),
            duration_ms=_ms(getattr(info, "duration", None)) or None,
            segments=segments,
            parameters=params,
        )
