from __future__ import annotations

from pathlib import Path

import pytest

from vme.config import load_settings


def test_settings_read_env_and_hide_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # no stray .env
    monkeypatch.setenv("VME_DB_PATH", str(tmp_path / "x" / "vme.sqlite3"))
    monkeypatch.setenv("VME_LLM_MODEL_STRONG", "alias-only")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    s = load_settings()
    assert s.db_path == tmp_path / "x" / "vme.sqlite3"
    assert s.llm_model_strong == "alias-only"
    assert "sk-test-123" not in repr(s)
    assert s.secret_values() == ("sk-test-123",)
    assert (s.render_width, s.render_height) == (1080, 1920)


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for key in ("VME_DB_PATH", "ANTHROPIC_API_KEY", "TRANSCRIPTION_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    s = load_settings()
    assert s.db_path == Path("./artifacts/vme.sqlite3")
    assert s.secret_values() == ()
    assert s.ffprobe_bin == "ffprobe"


def test_env_example_keys_are_all_known() -> None:
    """``.env.example`` is the only configuration reference: every key must map to a field."""
    aliases = {
        f.validation_alias if isinstance(f.validation_alias, str) else None
        for f in load_settings().__class__.model_fields.values()
    }
    text = Path(__file__).resolve().parents[1].joinpath(".env.example").read_text()
    keys = {
        line.split("=", 1)[0]
        for line in text.splitlines()
        if "=" in line and not line.startswith("#")
    }
    # Phase 3 / Phase 2+ keys are documented but not yet consumed by code.
    later = {"YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "DATABASE_URL"}
    assert keys - later <= aliases
