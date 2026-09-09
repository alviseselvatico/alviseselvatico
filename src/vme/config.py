"""Typed settings. ``.env.example`` is the only configuration reference.

Values are read from the environment (and a local ``.env`` if present). Secrets are
``SecretStr`` so they never appear in ``repr``/logs by accident.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # --- LLM (D012): aliases only, resolved at runtime; no model ID lives in code.
    llm_provider: str = Field(default="anthropic", validation_alias="VME_LLM_PROVIDER")
    llm_model_cheap: str = Field(default="", validation_alias="VME_LLM_MODEL_CHEAP")
    llm_model_strong: str = Field(default="", validation_alias="VME_LLM_MODEL_STRONG")
    anthropic_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="ANTHROPIC_API_KEY"
    )
    llm_max_tokens: int = Field(default=8192, validation_alias="VME_LLM_MAX_TOKENS", ge=256)
    llm_max_attempts: int = Field(default=2, validation_alias="VME_LLM_MAX_ATTEMPTS", ge=1, le=5)
    llm_effort: str = Field(default="", validation_alias="VME_LLM_EFFORT")

    # --- Ranking (Viral Score v0)
    ranking_finalists: int = Field(default=5, validation_alias="VME_RANKING_FINALISTS", ge=1)
    ranking_weights_path: Path | None = Field(
        default=None, validation_alias="VME_RANKING_WEIGHTS_PATH"
    )
    vertical: str = Field(default="general", validation_alias="VME_VERTICAL")
    audience: str = Field(
        default="general English-speaking short-form viewers", validation_alias="VME_AUDIENCE"
    )

    # --- Editorial / review
    target_clip_ms: int = Field(default=45_000, validation_alias="VME_TARGET_CLIP_MS", ge=1000)
    reviewer: str = Field(default="", validation_alias="VME_REVIEWER")

    # --- Transcription (D011)
    stt_provider: str = Field(default="faster_whisper", validation_alias="VME_STT_PROVIDER")
    stt_model_size: str = Field(default="small", validation_alias="VME_STT_MODEL_SIZE")
    stt_device: str = Field(default="cpu", validation_alias="VME_STT_DEVICE")
    stt_compute_type: str = Field(default="int8", validation_alias="VME_STT_COMPUTE_TYPE")
    stt_language: str = Field(default="", validation_alias="VME_STT_LANGUAGE")
    stt_beam_size: int = Field(default=5, validation_alias="VME_STT_BEAM_SIZE", ge=1, le=20)
    transcription_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="TRANSCRIPTION_API_KEY"
    )

    # --- Storage / paths
    db_path: Path = Field(default=Path("./artifacts/vme.sqlite3"), validation_alias="VME_DB_PATH")
    artifacts_dir: Path = Field(default=Path("./artifacts"), validation_alias="VME_ARTIFACTS_DIR")

    # --- Rendering (D013)
    render_width: int = Field(default=1080, validation_alias="VME_RENDER_WIDTH", ge=16)
    render_height: int = Field(default=1920, validation_alias="VME_RENDER_HEIGHT", ge=16)

    # --- External binaries (argument lists only, never shell strings)
    ffmpeg_bin: str = Field(default="ffmpeg", validation_alias="VME_FFMPEG_BIN")
    ffprobe_bin: str = Field(default="ffprobe", validation_alias="VME_FFPROBE_BIN")

    # --- Logging
    log_level: str = Field(default="INFO", validation_alias="VME_LOG_LEVEL")

    def secret_values(self) -> tuple[str, ...]:
        """Non-empty secret values, for log redaction."""
        return tuple(
            s.get_secret_value()
            for s in (self.anthropic_api_key, self.transcription_api_key)
            if s.get_secret_value()
        )


def load_settings(**overrides: object) -> Settings:
    """Build settings from the environment, with explicit keyword overrides for tests."""
    return Settings(**overrides)  # type: ignore[arg-type]
