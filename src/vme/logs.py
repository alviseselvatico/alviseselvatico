"""Structured JSON logging with a correlation id per CLI invocation.

Rules (CLAUDE.md, ARCHITECTURE §11):

* one JSON object per line on stderr;
* every record carries ``correlation_id``;
* known secret values are redacted everywhere in the record;
* media paths are logged through :func:`display_path`, which keeps the full path only
  when it lies inside the artifacts directory and otherwise keeps the file name only.
"""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

_correlation_id: ContextVar[str] = ContextVar("vme_correlation_id", default="-")
_REDACTED = "[REDACTED]"
_SECRET_KEY_HINTS = ("key", "token", "secret", "password", "authorization", "credential")

_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {
    "message",
    "asctime",
    "taskName",
}


def new_correlation_id() -> str:
    cid = f"cid_{secrets.token_hex(6)}"
    _correlation_id.set(cid)
    return cid


def set_correlation_id(cid: str) -> None:
    _correlation_id.set(cid)


def correlation_id() -> str:
    return _correlation_id.get()


def display_path(path: Path | str, artifacts_dir: Path) -> str:
    """Path representation safe for logs: relative inside artifacts, basename elsewhere."""
    p = Path(path)
    try:
        return str(p.resolve().relative_to(artifacts_dir.resolve()))
    except (ValueError, OSError):
        return p.name


class JsonFormatter(logging.Formatter):
    def __init__(self, secret_values: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._secrets = tuple(s for s in secret_values if s)

    def _scrub(self, value: Any, key: str = "") -> Any:
        if isinstance(value, str):
            if key and any(h in key.lower() for h in _SECRET_KEY_HINTS):
                return _REDACTED
            for s in self._secrets:
                if s in value:
                    value = value.replace(s, _REDACTED)
            return value
        if isinstance(value, Mapping):
            return {str(k): self._scrub(v, str(k)) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self._scrub(v) for v in value]
        if isinstance(value, int | float | bool) or value is None:
            return value
        return self._scrub(str(value), key)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": correlation_id(),
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(self._scrub(payload), ensure_ascii=False, default=str)


def configure_logging(
    level: str = "INFO",
    stream: IO[str] | None = None,
    secret_values: tuple[str, ...] = (),
) -> logging.Logger:
    """Install the JSON handler on the ``vme`` logger (idempotent)."""
    logger = logging.getLogger("vme")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(secret_values))
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"vme.{name}")
