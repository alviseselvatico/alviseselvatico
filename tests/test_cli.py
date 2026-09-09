"""End-to-end CLI tests run in-process through ``vme.cli.main.run``."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import requires_ffmpeg
from vme.cli.main import EXIT_BLOCKED, EXIT_ERROR, EXIT_OK, EXIT_USAGE, run


def _vme(*argv: str) -> tuple[int, Any, list[dict[str, Any]]]:
    out, err = io.StringIO(), io.StringIO()
    code = run(list(argv), out=out, err=err)
    payload = json.loads(out.getvalue()) if out.getvalue().strip() else None
    logs = [json.loads(line) for line in err.getvalue().splitlines() if line.startswith("{")]
    return code, payload, logs


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VME_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("VME_DB_PATH", str(tmp_path / "artifacts" / "vme.sqlite3"))
    return tmp_path / "artifacts" / "vme.sqlite3"


def test_usage_error_exit_code() -> None:
    code, payload, _ = _vme("source")
    assert code == EXIT_USAGE and payload is None


def test_db_migrate(db: Path) -> None:
    code, payload, logs = _vme("db", "migrate")
    assert code == EXIT_OK and payload == {"applied": [], "current": [1]}  # Store.open migrated
    assert logs[0]["event"] == "cli_start" and logs[-1]["status"] == "success"
    assert all(log["correlation_id"] == logs[0]["correlation_id"] for log in logs)
    assert db.is_file()


@requires_ffmpeg
def test_end_to_end_owned_then_unknown(db: Path, audio_wav: Path, video_mp4: Path) -> None:
    code, src, _ = _vme("source", "add", "--id", "S001", "--uri", str(audio_wav), "--title", "t")
    assert code == EXIT_OK and src["id"] == "S001" and src["rights_policy_id"] is None

    # No policy yet: blocked, file untouched.
    code, payload, logs = _vme("media", "register", "--source", "S001", str(audio_wav))
    assert code == EXIT_BLOCKED and payload["error"] == "blocked_policy"
    assert payload["reason_code"] == "no_policy"
    assert logs[-1]["status"] == "blocked_policy"

    code, pol, _ = _vme(
        "policy", "add", "--source", "S001", "--basis", "owned",
        "--reference", "docs/SOURCES.md#S001", "--can-ingest", "--can-extract-clip",
        "--can-transform",
    )  # fmt: skip
    assert code == EXIT_OK and pol["policy"]["basis_type"] == "OWNED"
    assert pol["source"]["rights_policy_id"] == pol["policy"]["id"]

    code, asset, logs = _vme("media", "register", "--source", "S001", str(audio_wav))
    assert code == EXIT_OK and asset["source_id"] == "S001" and len(asset["sha256"]) == 64
    assert asset["audio_codec"] == "pcm_s16le"
    # full media path outside artifacts never appears in logs
    joined = json.dumps(logs)
    assert str(audio_wav.parent) not in joined and audio_wav.name in joined

    code, shown, _ = _vme("source", "show", "S001")
    assert code == EXIT_OK and shown["source"]["status"] == "INGESTED"
    assert shown["gate"] == {
        "ingest": True, "clip": True, "render_transform": True, "publish": False
    }  # fmt: skip
    assert [m["id"] for m in shown["media"]] == [asset["id"]]

    code, same, _ = _vme("media", "show", asset["id"])
    assert code == EXIT_OK and same == asset

    # Same file under a second source whose policy is UNKNOWN: refused.
    code, _, _ = _vme("source", "add", "--id", "S002", "--uri", str(audio_wav))
    assert code == EXIT_OK
    code, _, _ = _vme("policy", "add", "--source", "S002", "--basis", "unknown")
    assert code == EXIT_OK
    code, payload, _ = _vme("media", "register", "--source", "S002", str(audio_wav))
    assert code == EXIT_BLOCKED and payload["reason_code"] == "ingest_not_permitted"
    code, listing, _ = _vme("media", "list")
    assert code == EXIT_OK and [m["source_id"] for m in listing] == ["S001"]

    # Video under the OWNED source still works and reports dimensions.
    code, vid, _ = _vme("media", "register", "--source", "S001", str(video_mp4))
    assert code == EXIT_OK and (vid["width"], vid["height"]) == (320, 180)


def test_policy_add_rejects_d009_violations_with_exit_1(db: Path) -> None:
    _vme("source", "add", "--id", "S001", "--uri", "x.wav")
    code, payload, logs = _vme(
        "policy", "add", "--source", "S001", "--basis", "blocked", "--can-ingest"
    )
    assert code == EXIT_ERROR and payload["error"] == "validation_error"
    assert "D009" in payload["errors"][0]["msg"]
    assert logs[-1]["status"] == "permanent_failure"
    code, payload, _ = _vme(
        "policy", "add", "--source", "S001", "--basis", "transformative_review_required",
        "--reference", "r", "--can-ingest", "--can-publish", "--review-required",
    )  # fmt: skip
    assert code == EXIT_ERROR and "can_publish" in payload["errors"][0]["msg"]
    code, shown, _ = _vme("source", "show", "S001")
    assert shown["rights_policy"] is None  # nothing attached after failures


def test_expired_policy_blocks_registration(db: Path, tmp_path: Path) -> None:
    _vme("source", "add", "--id", "S001", "--uri", "x.wav")
    code, _, _ = _vme(
        "policy", "add", "--source", "S001", "--basis", "explicit_license", "--reference", "r",
        "--can-ingest", "--expiry", "2020-01-01T00:00:00Z",
    )  # fmt: skip
    assert code == EXIT_OK
    code, payload, _ = _vme("media", "register", "--source", "S001", str(tmp_path / "nope.wav"))
    assert code == EXIT_BLOCKED and payload["reason_code"] == "policy_expired"


def test_unknown_source_is_an_error(db: Path) -> None:
    code, payload, _ = _vme("source", "show", "ghost")
    assert code == EXIT_ERROR and payload["error"] == "NotFoundError"
