"""Shared fixtures. Media fixtures are generated at test time with ``ffmpeg -f lavfi`` (D016)."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vme.domain.models import BasisType, RightsPolicy, Source, SourceKind
from vme.storage.db import Store

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

requires_ffmpeg = pytest.mark.skipif(
    FFMPEG is None or FFPROBE is None,
    reason="ffmpeg/ffprobe not installed: media fixtures cannot be generated (D016)",
)


def _lavfi(out: Path, *args: str) -> Path:
    assert FFMPEG is not None
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", *args, str(out)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    assert out.is_file() and out.stat().st_size > 0
    return out


@pytest.fixture
def audio_wav(tmp_path: Path) -> Path:
    """1 s mono 16 kHz sine tone."""
    return _lavfi(
        tmp_path / "tone.wav",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-ar", "16000", "-ac", "1",
    )  # fmt: skip


@pytest.fixture
def video_mp4(tmp_path: Path) -> Path:
    """2 s 320x180 test pattern with a sine audio track, H.264 + AAC."""
    return _lavfi(
        tmp_path / "pattern.mp4",
        "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
    )  # fmt: skip


@pytest.fixture
def store() -> Iterator[Store]:
    s = Store.open(":memory:")
    yield s
    s.close()


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "artifacts"
    d.mkdir()
    return d


def make_policy(basis: BasisType = BasisType.OWNED, **overrides: object) -> RightsPolicy:
    base: dict[str, object] = {
        "id": f"pol_{basis.value.lower()}",
        "basis_type": basis,
        "basis_reference": None,
        "created_at": NOW,
    }
    if basis not in (BasisType.BLOCKED, BasisType.UNKNOWN):
        base.update(
            basis_reference="docs/SOURCES.md#S001",
            can_ingest=True,
            can_extract_clip=True,
            can_transform=True,
        )
    if basis is BasisType.TRANSFORMATIVE_REVIEW_REQUIRED:
        base["review_required"] = True
    base.update(overrides)
    return RightsPolicy.model_validate(base)


def make_source(source_id: str = "S001", policy_id: str | None = None) -> Source:
    return Source(
        id=source_id,
        kind=SourceKind.LOCAL_FILE,
        canonical_uri="fixtures/speech/sample_en.wav",
        publisher="operator",
        title="fixture",
        created_at=NOW,
        rights_policy_id=policy_id,
    )
