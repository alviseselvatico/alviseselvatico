"""Transcription: provider protocol, adapters and the rights-checked transcribe service."""

from vme.transcription.base import SpeechToText, TranscriptionError, TranscriptionResult
from vme.transcription.factory import build_transcriber
from vme.transcription.service import transcribe_media

__all__ = [
    "SpeechToText",
    "TranscriptionError",
    "TranscriptionResult",
    "build_transcriber",
    "transcribe_media",
]
