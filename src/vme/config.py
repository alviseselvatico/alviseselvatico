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

    # --- Transcription (D011)
    stt_provider: str = Field(default="faster_whisper", validation_alias="VME_STT_PROVIDER")
    stt_model_size: str = Field(default="small", validation_alias="VME_STT_MODEL_SIZE")
    stt_device: str = Field(default="cpu", validation_alias="VME_STT_DEVICE")
    stt_compute_type: str = Field(default="int8", validation_alias="VME_STT_COMPUTE_TYPE")
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
