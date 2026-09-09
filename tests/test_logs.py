from __future__ import annotations

import io
import json
from pathlib import Path

from vme.logs import configure_logging, correlation_id, display_path, new_correlation_id


def _records(buf: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line]


def test_json_lines_with_correlation_id_and_extras() -> None:
    buf = io.StringIO()
    logger = configure_logging("DEBUG", stream=buf)
    cid = new_correlation_id()
    logger.getChild("x").info("hello", extra={"source_id": "S001", "n": 3})
    (rec,) = _records(buf)
    assert rec["event"] == "hello"
    assert rec["correlation_id"] == cid == correlation_id()
    assert rec["source_id"] == "S001" and rec["n"] == 3
    assert rec["level"] == "INFO" and rec["logger"] == "vme.x"
    assert str(rec["ts"]).endswith("+00:00")


def test_secret_values_and_secret_keys_are_redacted() -> None:
    buf = io.StringIO()
    logger = configure_logging("INFO", stream=buf, secret_values=("sk-ant-very-secret",))
    logger.info(
        "auth failed for sk-ant-very-secret",
        extra={"api_key": "anything", "nested": {"token": "t", "ok": "fine"}},
    )
    text = buf.getvalue()
    assert "sk-ant-very-secret" not in text
    assert "anything" not in text
    (rec,) = _records(buf)
    assert rec["event"] == "auth failed for [REDACTED]"
    assert rec["api_key"] == "[REDACTED]"
    assert rec["nested"] == {"token": "[REDACTED]", "ok": "fine"}


def test_display_path_hides_paths_outside_artifacts(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    (artifacts / "source").mkdir(parents=True)
    inside = artifacts / "source" / "a.wav"
    outside = tmp_path / "private" / "home" / "video.mp4"
    assert display_path(inside, artifacts) == "source/a.wav"
    assert display_path(outside, artifacts) == "video.mp4"
    assert "private" not in display_path(outside, artifacts)


def test_configure_is_idempotent() -> None:
    buf = io.StringIO()
    configure_logging("INFO", stream=buf)
    logger = configure_logging("INFO", stream=buf)
    logger.info("once")
    assert len(_records(buf)) == 1
