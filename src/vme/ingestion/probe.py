"""ffprobe metadata via ``subprocess`` with an argument list (never a shell string)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TIMEOUT_S = 60


class ProbeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProbeResult:
    duration_ms: int | None
    width: int | None
    height: int | None
    audio_codec: str | None
    video_codec: str | None
    format_name: str | None
    raw: dict[str, Any]


def _first_stream(streams: list[dict[str, Any]], codec_type: str) -> dict[str, Any] | None:
    for s in streams:
        if s.get("codec_type") == codec_type:
            return s
    return None


def _duration_ms(fmt: dict[str, Any], streams: list[dict[str, Any]]) -> int | None:
    candidates = [fmt.get("duration"), *(s.get("duration") for s in streams)]
    for value in candidates:
        if value in (None, "", "N/A"):
            continue
        try:
            return round(float(value) * 1000)
        except (TypeError, ValueError):
            continue
    return None


def probe_media(path: Path, ffprobe_bin: str = "ffprobe") -> ProbeResult:
    """Run ffprobe and return typed metadata. Raises :class:`ProbeError` on any failure."""
    if not path.is_file():
        msg = f"not a regular file: {path.name}"
        raise ProbeError(msg)
    argv = [
        ffprobe_bin,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "--",  # ffprobe accepts '--' as end-of-options; guards names starting with '-'
        str(path),
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, path from operator
            argv,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError as exc:
        msg = f"ffprobe binary not found: {ffprobe_bin!r}"
        raise ProbeError(msg) from exc
    except subprocess.TimeoutExpired as exc:
        msg = f"ffprobe timed out after {_TIMEOUT_S}s"
        raise ProbeError(msg) from exc
    if completed.returncode != 0:
        msg = f"ffprobe failed (exit {completed.returncode}): {completed.stderr.strip()[:500]}"
        raise ProbeError(msg)
    try:
        raw: dict[str, Any] = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        msg = "ffprobe returned invalid JSON"
        raise ProbeError(msg) from exc
    fmt: dict[str, Any] = raw.get("format") or {}
    streams: list[dict[str, Any]] = raw.get("streams") or []
    if not streams:
        msg = "ffprobe found no media streams"
        raise ProbeError(msg)
    video = _first_stream(streams, "video")
    audio = _first_stream(streams, "audio")
    return ProbeResult(
        duration_ms=_duration_ms(fmt, streams),
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        audio_codec=str(audio["codec_name"]) if audio and audio.get("codec_name") else None,
        video_codec=str(video["codec_name"]) if video and video.get("codec_name") else None,
        format_name=str(fmt["format_name"]) if fmt.get("format_name") else None,
        raw=raw,
    )
