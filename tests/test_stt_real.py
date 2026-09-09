"""Real faster-whisper run on a speech fixture. Skips with an explicit reason if no fixture
exists (D016/D019); never substitutes a fake transcript."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from vme.config import load_settings
from vme.transcription.factory import build_transcriber

ROOT = Path(__file__).resolve().parents[1]
OWNED = ROOT / "fixtures" / "speech" / "sample_en.wav"
EXTERNAL_JFK = ROOT / "fixtures" / "speech" / "external" / "jfk.wav"


def _fixture() -> Path | None:
    for p in (OWNED, EXTERNAL_JFK):
        if p.is_file():
            return p
    return None


@pytest.mark.skipif(
    _fixture() is None,
    reason=(
        "no speech fixture: record fixtures/speech/sample_en.wav or run "
        "`uv run python scripts/fetch_speech_fixtures.py` (D016/D019)"
    ),
)
def test_faster_whisper_tiny_transcribes_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    path = _fixture()
    assert path is not None
    monkeypatch.setenv("VME_STT_MODEL_SIZE", "tiny")
    monkeypatch.setenv("VME_STT_LANGUAGE", "en")
    stt = build_transcriber(load_settings())
    result = stt.transcribe(path)
    assert result.provider == "faster_whisper" and result.model_alias == "tiny"
    assert result.language == "en"
    assert result.segments and all(s.words for s in result.segments)
    words = [w for s in result.segments for w in s.words]
    assert all(w.end_ms >= w.start_ms for w in words)
    assert all(a.start_ms <= b.start_ms for a, b in pairwise(words))
    text = result.text.lower()
    if path == EXTERNAL_JFK:
        for phrase in ("my fellow americans", "your country", "do for you"):
            assert phrase in text
