from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

import pytest

from tests.conftest import NOW, make_policy, make_source, requires_ffmpeg
from vme.domain.models import BasisType, SourceStatus
from vme.ingestion.fingerprint import sha256_file
from vme.ingestion.probe import ProbeError, probe_media
from vme.ingestion.register import IngestionError, register_local_media
from vme.rights.gate import RightsBlockedError
from vme.storage.db import Store
from vme.storage.repositories import DuplicateRecordError


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    f = tmp_path / "blob.bin"
    data = bytes(range(256)) * 5000
    f.write_bytes(data)
    assert sha256_file(f) == hashlib.sha256(data).hexdigest()


@requires_ffmpeg
def test_probe_audio_only_wav(audio_wav: Path) -> None:
    r = probe_media(audio_wav)
    assert r.audio_codec == "pcm_s16le"
    assert r.video_codec is None and r.width is None and r.height is None
    assert r.duration_ms is not None and 900 <= r.duration_ms <= 1100
    assert r.format_name == "wav"


@requires_ffmpeg
def test_probe_video_mp4(video_mp4: Path) -> None:
    r = probe_media(video_mp4)
    assert (r.width, r.height) == (320, 180)
    assert r.video_codec == "h264" and r.audio_codec == "aac"
    assert r.duration_ms is not None and 1900 <= r.duration_ms <= 2200


@requires_ffmpeg
def test_probe_rejects_non_media_and_missing_files(tmp_path: Path) -> None:
    junk = tmp_path / "notes.txt"
    junk.write_text("hello")
    with pytest.raises(ProbeError, match="ffprobe failed"):
        probe_media(junk)
    with pytest.raises(ProbeError, match="not a regular file"):
        probe_media(tmp_path / "missing.mp4")


def test_probe_reports_missing_binary(tmp_path: Path) -> None:
    f = tmp_path / "x.wav"
    f.write_bytes(b"RIFF")
    with pytest.raises(ProbeError, match="binary not found"):
        probe_media(f, ffprobe_bin="definitely-not-ffprobe-xyz")


def _seed(store: Store, basis: BasisType | None, **policy_overrides: object) -> None:
    store.sources.add(make_source())
    if basis is not None:
        policy = make_policy(basis, **policy_overrides)
        store.policies.add(policy)
        store.sources.attach_policy("S001", policy.id)


@requires_ffmpeg
def test_register_owned_source(store: Store, audio_wav: Path, artifacts_dir: Path) -> None:
    _seed(store, BasisType.OWNED)
    asset = register_local_media(store, "S001", audio_wav, artifacts_dir=artifacts_dir, now=NOW)
    assert asset.sha256 == sha256_file(audio_wav)
    assert asset.audio_codec == "pcm_s16le" and asset.duration_ms
    assert asset.path_or_object_key == str(audio_wav.resolve())
    assert store.media.get(asset.id) == asset
    assert store.sources.get("S001").status is SourceStatus.INGESTED


@requires_ffmpeg
def test_register_same_file_twice_is_a_visible_duplicate(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    _seed(store, BasisType.OWNED)
    register_local_media(store, "S001", audio_wav, artifacts_dir=artifacts_dir, now=NOW)
    with pytest.raises(DuplicateRecordError, match="already registered"):
        register_local_media(store, "S001", audio_wav, artifacts_dir=artifacts_dir, now=NOW)
    assert len(store.media.list("S001")) == 1


@pytest.mark.parametrize(
    ("basis", "overrides", "reason"),
    [
        (None, {}, "no_policy"),
        (BasisType.UNKNOWN, {}, "ingest_not_permitted"),
        (BasisType.BLOCKED, {}, "ingest_not_permitted"),
        (BasisType.OWNED, {"expiry_at": NOW - timedelta(hours=1)}, "policy_expired"),
        (BasisType.EXPLICIT_LICENSE, {"can_ingest": False}, "ingest_not_permitted"),
    ],
)
def test_register_refused_before_touching_the_file(
    store: Store,
    tmp_path: Path,
    artifacts_dir: Path,
    basis: BasisType | None,
    overrides: dict[str, object],
    reason: str,
) -> None:
    _seed(store, basis, **overrides)
    missing = tmp_path / "never-read.mp4"  # does not exist: proves the gate runs first
    with pytest.raises(RightsBlockedError) as excinfo:
        register_local_media(store, "S001", missing, artifacts_dir=artifacts_dir, now=NOW)
    assert excinfo.value.decision.reason_code == reason
    assert store.media.list() == []
    assert store.sources.get("S001").status is SourceStatus.REGISTERED


def test_register_missing_file_after_gate(
    store: Store, tmp_path: Path, artifacts_dir: Path
) -> None:
    _seed(store, BasisType.OWNED)
    with pytest.raises(IngestionError, match="not found"):
        register_local_media(
            store, "S001", tmp_path / "ghost.mp4", artifacts_dir=artifacts_dir, now=NOW
        )
