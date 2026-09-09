"""Resolve the configured speech-to-text provider (D005). Unknown provider = visible error."""

from __future__ import annotations

from vme.config import Settings
from vme.transcription.base import SpeechToText, TranscriptionError
from vme.transcription.faster_whisper_adapter import PROVIDER as FASTER_WHISPER
from vme.transcription.faster_whisper_adapter import FasterWhisperTranscriber


def build_transcriber(settings: Settings) -> SpeechToText:
    if settings.stt_provider == FASTER_WHISPER:
        return FasterWhisperTranscriber(
            model_size=settings.stt_model_size,
            device=settings.stt_device,
            compute_type=settings.stt_compute_type,
            language=settings.stt_language or None,
            beam_size=settings.stt_beam_size,
        )
    msg = f"unsupported VME_STT_PROVIDER={settings.stt_provider!r} (known: {FASTER_WHISPER})"
    raise TranscriptionError(msg)
