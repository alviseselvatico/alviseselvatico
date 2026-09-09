"""End-to-end CLI tests run in-process through ``vme.cli.main.run``."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import requires_ffmpeg
from tests.fake_llm import FakeLlm
from tests.fake_stt import FakeSpeechToText
from vme.cli.main import EXIT_BLOCKED, EXIT_ERROR, EXIT_OK, EXIT_USAGE, run
from vme.storage.migrations import MIGRATIONS


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
    versions = [m.version for m in MIGRATIONS]
    assert code == EXIT_OK and payload == {
        "applied": [],
        "current": versions,
    }  # Store.open migrated
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


@requires_ffmpeg
def test_transcribe_and_segment_pipeline(
    db: Path, audio_wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import vme.cli.main as cli

    monkeypatch.setattr(cli, "build_transcriber", lambda _settings: FakeSpeechToText(pause_ms=300))
    _vme("source", "add", "--id", "S001", "--uri", str(audio_wav))
    _vme(
        "policy", "add", "--source", "S001", "--basis", "owned", "--reference", "r",
        "--can-ingest", "--can-extract-clip", "--can-transform",
    )  # fmt: skip
    code, asset, _ = _vme("media", "register", "--source", "S001", str(audio_wav))
    assert code == EXIT_OK

    code, summary, logs = _vme("transcribe", "--media", asset["id"])
    assert code == EXIT_OK and summary["kind"] == "RAW" and summary["version"] == 1
    assert summary["provider"] == "fake" and summary["segments"] == 8 and summary["words"] > 8
    assert any(log["event"] == "transcript_created" for log in logs)

    code, full, _ = _vme("transcript", "show", summary["id"], "--full")
    assert code == EXIT_OK and len(full["segments"]) == 8 and full["segments"][0]["words"]
    code, listing, _ = _vme("transcript", "list", "--media", asset["id"])
    assert code == EXIT_OK and [t["id"] for t in listing] == [summary["id"]]

    code, cands, logs = _vme(
        "segment", "--transcript", summary["id"], "--min-ms", "3000", "--target-ms", "6000",
        "--max-ms", "9000",
    )  # fmt: skip
    assert code == EXIT_OK and len(cands) >= 2
    assert all(c["created_by"] == "segmenter:v0.1.0" for c in cands)
    assert logs[-2]["event"] == "candidates_created"
    code, again, _ = _vme("segment", "--transcript", summary["id"], "--min-ms", "3000",
                          "--target-ms", "6000", "--max-ms", "9000")  # fmt: skip
    assert code == EXIT_ERROR and again["error"] == "DuplicateRecordError"
    code, listed, _ = _vme("candidate", "list", "--transcript", summary["id"])
    assert code == EXIT_OK and listed == cands
    code, one, _ = _vme("candidate", "show", cands[0]["id"])
    assert code == EXIT_OK and one == cands[0]

    # invalid segmenter config is a visible error, not a crash
    code, bad, _ = _vme(
        "segment", "--transcript", summary["id"], "--min-ms", "9", "--target-ms", "5"
    )
    assert code == EXIT_ERROR and bad["error"] == "ValueError"


def test_transcribe_unknown_media_and_unsupported_provider(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VME_STT_PROVIDER", "nope")
    code, payload, _ = _vme("transcribe", "--media", "ghost")
    assert code == EXIT_ERROR and payload["error"] == "TranscriptionError"


@requires_ffmpeg
def test_rank_pipeline_cli(db: Path, audio_wav: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import vme.cli.main as cli

    sentences = [
        f"Sentence {i} explains a market idea with enough words to pass." for i in range(10)
    ]
    monkeypatch.setattr(
        cli, "build_transcriber", lambda _s: FakeSpeechToText(sentences, pause_ms=200)
    )
    fake = FakeLlm()
    monkeypatch.setattr(cli, "build_llm", lambda _s: fake)
    _vme("source", "add", "--id", "S001", "--uri", str(audio_wav))
    _vme(
        "policy", "add", "--source", "S001", "--basis", "owned", "--reference", "r", "--can-ingest"
    )
    _, asset, _ = _vme("media", "register", "--source", "S001", str(audio_wav))
    _, transcript, _ = _vme("transcribe", "--media", asset["id"])
    _, cands, _ = _vme(
        "segment",
        "--transcript",
        transcript["id"],
        "--min-ms",
        "3000",
        "--target-ms",
        "5000",
        "--max-ms",
        "8000",
    )
    assert len(cands) >= 3

    code, ranked, logs = _vme(
        "rank", "--transcript", transcript["id"], "--finalists", "2", "--min-ms", "1000",
        "--min-words", "5",
    )  # fmt: skip
    assert code == EXIT_OK, ranked
    assert ranked["strong_scored"] == 2 and ranked["cheap_scored"] == len(cands)
    assert ranked["runs"][0]["final_score"] >= ranked["runs"][-1]["final_score"]
    assert any(log["event"] == "ranking_batch_created" for log in logs)
    batch_id = ranked["batch"]["id"]

    code, shown, _ = _vme("ranking", "show", batch_id)
    assert code == EXIT_OK and [r["id"] for r in shown["runs"]] == [r["id"] for r in ranked["runs"]]
    code, batches, _ = _vme("ranking", "list", "--transcript", transcript["id"])
    assert code == EXIT_OK and [b["id"] for b in batches] == [batch_id]
    code, calls, _ = _vme("llm", "list", "--purpose", "candidate_scoring")
    assert code == EXIT_OK and len(calls) == len(ranked["llm_calls"]) and "response" not in calls[0]
    code, one, _ = _vme("llm", "show", calls[0]["id"])
    assert code == EXIT_OK and one["response"]["hook_strength"] <= 1.0

    # no candidates for an unknown segmenter -> visible error
    code, err, _ = _vme("rank", "--transcript", transcript["id"], "--created-by", "nobody")
    assert code == EXIT_ERROR and err["error"] == "RankingError"


def test_rank_without_models_configured_is_visible_error(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VME_LLM_MODEL_STRONG", raising=False)
    code, payload, _ = _vme("rank", "--transcript", "ghost")
    assert code == EXIT_ERROR and payload["error"] == "NotFoundError"


@requires_ffmpeg
def test_editorial_and_review_cli(
    db: Path, audio_wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import vme.cli.main as cli

    sentences = [
        f"Sentence {i} explains a market idea with enough words to pass." for i in range(6)
    ]
    monkeypatch.setattr(
        cli, "build_transcriber", lambda _s: FakeSpeechToText(sentences, pause_ms=200)
    )
    monkeypatch.setattr(
        cli, "build_llm", lambda _s: FakeLlm(editorial_claims=1, extractor_claims=1)
    )
    monkeypatch.setenv("VME_REVIEWER", "operator-1")
    _vme("source", "add", "--id", "S001", "--uri", str(audio_wav))
    _vme("policy", "add", "--source", "S001", "--basis", "owned", "--reference", "r",
         "--can-ingest", "--can-extract-clip", "--can-transform")  # fmt: skip
    _, asset, _ = _vme("media", "register", "--source", "S001", str(audio_wav))
    _, transcript, _ = _vme("transcribe", "--media", asset["id"])
    _, cands, _ = _vme("segment", "--transcript", transcript["id"], "--min-ms", "4000",
                       "--target-ms", "8000", "--max-ms", "12000")  # fmt: skip

    code, gen, logs = _vme(
        "editorial", "generate", "--candidate", cands[0]["id"], "--target-ms", "30000"
    )
    assert code == EXIT_OK, gen
    assert gen["draft"]["status"] == "blocked_factcheck" and len(gen["claims"]) == 1
    assert any(log["event"] == "editorial_generated" for log in logs)
    draft_id = gen["draft"]["id"]

    code, res, _ = _vme("review", "approve", draft_id)
    assert code == EXIT_ERROR and res["error"] == "ReviewError"

    code, res, _ = _vme(
        "claim", "resolve", gen["claims"][0]["id"], "--status", "human-approved", "--note", "ok"
    )
    assert code == EXIT_OK and res["draft_status"] == "needs_review"
    assert res["event"]["reviewer"] == "operator-1"

    code, shown, _ = _vme("editorial", "show", draft_id)
    assert code == EXIT_OK and shown["draft"]["status"] == "needs_review"
    assert [e["decision"] for e in shown["events"]] == ["factcheck_cleared"]

    code, res, _ = _vme("review", "approve", draft_id, "--reviewer", "operator-2")
    assert (
        code == EXIT_OK
        and res["draft"]["status"] == "approved"
        and res["event"]["reviewer"] == "operator-2"
    )
    code, events, _ = _vme(
        "review", "events", "--object-type", "editorial_version", "--object-id", draft_id
    )
    assert code == EXIT_OK and [e["decision"] for e in events] == ["factcheck_cleared", "approve"]
    code, listed, _ = _vme("editorial", "list", "--candidate", cands[0]["id"])
    assert code == EXIT_OK and [d["id"] for d in listed] == [draft_id]

    # second draft rejected with reasons
    _, gen2, _ = _vme("editorial", "generate", "--candidate", cands[0]["id"])
    _vme("claim", "resolve", gen2["claims"][0]["id"], "--status", "removed")
    code, res, _ = _vme(
        "review",
        "reject",
        gen2["draft"]["id"],
        "--reason",
        "weak_hook,off_topic",
        "--reason",
        "too_long",
    )
    assert code == EXIT_OK and res["event"]["reason_codes"] == [
        "weak_hook",
        "off_topic",
        "too_long",
    ]
    code, res, _ = _vme("review", "recheck", gen2["draft"]["id"])
    assert code == EXIT_OK and res["changed"] is False

    # reviewer identity is mandatory
    monkeypatch.delenv("VME_REVIEWER")
    code, res, _ = _vme("claim", "resolve", gen2["claims"][0]["id"], "--status", "removed")
    assert code == EXIT_ERROR and res["error"] == "ReviewError"


@requires_ffmpeg
def test_render_cli(db: Path, video_mp4_10s: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import vme.cli.main as cli

    sentences = ["Ask not what your country can do.", "Ask what you can do for it, honestly?"]
    monkeypatch.setattr(
        cli, "build_transcriber", lambda _s: FakeSpeechToText(sentences, pause_ms=100)
    )
    monkeypatch.setattr(
        cli, "build_llm", lambda _s: FakeLlm(editorial_claims=0, extractor_claims=0)
    )
    monkeypatch.setenv("VME_REVIEWER", "operator-1")
    monkeypatch.setenv("VME_RENDER_PRESET", "ultrafast")
    _vme("source", "add", "--id", "S001", "--uri", str(video_mp4_10s))
    _vme("policy", "add", "--source", "S001", "--basis", "owned", "--reference", "r",
         "--can-ingest", "--can-extract-clip", "--can-transform")  # fmt: skip
    _, asset, _ = _vme("media", "register", "--source", "S001", str(video_mp4_10s))
    _, transcript, _ = _vme("transcribe", "--media", asset["id"])
    _, cands, _ = _vme("segment", "--transcript", transcript["id"], "--min-ms", "2000",
                       "--target-ms", "20000", "--max-ms", "30000")  # fmt: skip
    _, gen, _ = _vme("editorial", "generate", "--candidate", cands[0]["id"])
    draft_id = gen["draft"]["id"]

    code, err, _ = _vme("render", "plan", "--draft", draft_id)
    assert code == EXIT_ERROR and err["error"] == "PlanError"  # not approved yet
    code, res, _ = _vme("review", "approve", draft_id)
    assert code == EXIT_OK and res["draft"]["status"] == "approved"

    code, plan, _ = _vme(
        "render", "plan", "--draft", draft_id, "--hook-ms", "1000", "--outro-ms", "1000"
    )
    assert code == EXIT_OK and [i["kind"] for i in plan["timeline"]] == ["card", "source", "card"]
    code, plans, _ = _vme("render", "plans", "--draft", draft_id)
    assert code == EXIT_OK and plans[0]["id"] == plan["id"] and plans[0]["items"] == 3

    code, render, logs = _vme("render", "run", "--plan", plan["id"])
    assert code == EXIT_OK, render
    assert render["validation"]["passed"] and Path(render["file_path"]).is_file()
    assert logs[-2]["event"] == "render_completed" and logs[-2]["file"].startswith("renders/")
    code, shown, _ = _vme("render", "show", render["id"])
    assert code == EXIT_OK and shown["plan"]["id"] == plan["id"]
    code, listed, _ = _vme("render", "list", "--plan", plan["id"])
    assert code == EXIT_OK and [r["id"] for r in listed] == [render["id"]]


@requires_ffmpeg
def test_pipeline_cli(db: Path, audio_wav: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import vme.cli.main as cli

    sentences = [
        f"Sentence {i} shares a concrete market observation worth quoting." for i in range(12)
    ]
    monkeypatch.setattr(
        cli, "build_transcriber", lambda _s: FakeSpeechToText(sentences, pause_ms=200)
    )
    monkeypatch.setattr(
        cli, "build_llm", lambda _s: FakeLlm(editorial_claims=0, extractor_claims=1)
    )
    _vme("source", "add", "--id", "S001", "--uri", str(audio_wav))
    _vme("policy", "add", "--source", "S001", "--basis", "owned", "--reference", "r",
         "--can-ingest", "--can-extract-clip", "--can-transform")  # fmt: skip
    code, res, logs = _vme(
        "pipeline", "run", "--source", "S001", "--path", str(audio_wav), "--top", "2",
        "--finalists", "2", "--seg-min-ms", "4000", "--seg-target-ms", "6000",
        "--seg-max-ms", "9000", "--min-ms", "1000", "--min-words", "5",
    )  # fmt: skip
    assert code == EXIT_OK, res
    assert res["ok"] and [s["stage"] for s in res["stages"]] == [
        "register",
        "transcribe",
        "segment",
        "rank",
        "editorial",
    ]
    assert len(res["draft_ids"]) == 2 and res["candidates"] > 2
    cids = {log["correlation_id"] for log in logs}
    assert len(cids) == 1  # one correlation id across all stages
    assert sum(1 for log in logs if log["event"] == "pipeline_stage") == 5


@requires_ffmpeg
def test_label_golden_bench_cli(
    db: Path, audio_wav: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import vme.cli.main as cli

    sentences = [
        f"Sentence {i} shares a concrete market observation worth quoting." for i in range(12)
    ]
    monkeypatch.setattr(
        cli, "build_transcriber", lambda _s: FakeSpeechToText(sentences, pause_ms=200)
    )
    monkeypatch.setattr(cli, "build_llm", lambda _s: FakeLlm())
    monkeypatch.setenv("VME_REVIEWER", "op")
    _vme("source", "add", "--id", "S001", "--uri", str(audio_wav))
    _vme(
        "policy",
        "add",
        "--source",
        "S001",
        "--basis",
        "owned",
        "--reference",
        "r",
        "--can-ingest",
        "--can-extract-clip",
        "--can-transform",
    )
    _, res, _ = _vme(
        "pipeline",
        "run",
        "--source",
        "S001",
        "--path",
        str(audio_wav),
        "--top",
        "1",
        "--finalists",
        "2",
        "--seg-min-ms",
        "4000",
        "--seg-target-ms",
        "6000",
        "--seg-max-ms",
        "9000",
        "--min-ms",
        "1000",
        "--min-words",
        "5",
    )
    batch = res["batch_id"]
    _, shown, _ = _vme("ranking", "show", batch)
    ordered = [r["candidate_id"] for r in shown["runs"]]

    code, tax, _ = _vme("label", "taxonomy")
    assert code == EXIT_OK and "weak_hook" in tax["codes"]
    code, err, _ = _vme(
        "label", "add", "--candidate", ordered[0], "--decision", "reject", "--reason", "meh"
    )
    assert code == EXIT_ERROR and err["error"] == "UnknownReasonError"
    code, lb, _ = _vme(
        "label",
        "add",
        "--candidate",
        ordered[0],
        "--decision",
        "approve",
        "--hook",
        "5",
        "--boundary-correct",
        "--expected",
        "high",
        "--factual-risk",
        "1",
        "--rights-risk",
        "1",
    )
    assert code == EXIT_OK and lb["reviewer"] == "op" and lb["boundary_correct"] is True
    for cid in ordered[1:]:
        code, _, _ = _vme(
            "label",
            "add",
            "--candidate",
            cid,
            "--decision",
            "reject",
            "--reason",
            "low_novelty,too_short",
        )
        assert code == EXIT_OK
    code, listed, _ = _vme("label", "list", "--transcript", res["transcript_id"])
    assert code == EXIT_OK and len(listed) == len(ordered)

    out = tmp_path / "golden.jsonl"
    code, exp, _ = _vme("golden", "export", "--out", str(out), "--batch", batch)
    assert (
        code == EXIT_OK
        and exp["rows"] == len(ordered)
        and exp["approved"] == 1
        and exp["with_ranking_run"] == len(ordered)
    )
    assert len(out.read_text().splitlines()) == len(ordered)

    code, bench, logs = _vme("bench", "run", "--batch", batch)
    assert code == EXIT_OK and bench["metrics"]["precision_at_3"] == pytest.approx(1 / 3)
    assert any(log["event"] == "benchmark_recorded" for log in logs)
    code, benches, _ = _vme("bench", "list")
    assert code == EXIT_OK and benches[0]["id"] == bench["id"]
    code, cmp, _ = _vme("bench", "compare", bench["id"], bench["id"])
    assert code == EXIT_OK and cmp["regressed"] is False and cmp["same_versions"] is True
