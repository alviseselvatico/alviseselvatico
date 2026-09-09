"""Render a stored plan to an MP4 and validate it (ARCHITECTURE §3 ``rendering``)."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from vme.domain.models import DraftStatus, Render, RenderPlan, new_id, utc_now
from vme.editorial.service import policy_for_candidate
from vme.ingestion.fingerprint import sha256_file
from vme.ingestion.probe import probe_media
from vme.logs import display_path, get_logger
from vme.rendering.ffmpeg import RenderError, RenderSettings, build_command, run_ffmpeg
from vme.rights.gate import Action, require
from vme.storage.db import Store

log = get_logger("rendering")

_DURATION_TOLERANCE_MS = 1200


def _validate(
    plan: RenderPlan, probe: Any, expected_codecs: tuple[str, str] = ("h264", "aac")
) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "width": probe.width,
        "height": probe.height,
        "video_codec": probe.video_codec,
        "audio_codec": probe.audio_codec,
        "duration_ms": probe.duration_ms,
        "expected_duration_ms": plan.total_duration_ms,
    }
    problems: list[str] = []
    if (probe.width, probe.height) != (plan.width, plan.height):
        problems.append(f"size {probe.width}x{probe.height} != {plan.width}x{plan.height}")
    if probe.video_codec != expected_codecs[0]:
        problems.append(f"video codec {probe.video_codec} != {expected_codecs[0]}")
    if probe.audio_codec != expected_codecs[1]:
        problems.append(f"audio codec {probe.audio_codec} != {expected_codecs[1]}")
    if (
        probe.duration_ms is None
        or abs(probe.duration_ms - plan.total_duration_ms) > _DURATION_TOLERANCE_MS
    ):
        problems.append(
            f"duration {probe.duration_ms} ms not within tolerance of {plan.total_duration_ms} ms"
        )
    checks["problems"] = problems
    checks["passed"] = not problems
    return checks


def render_plan_to_file(
    store: Store,
    plan_id: str,
    settings: RenderSettings,
    *,
    artifacts_dir: Path,
    now: datetime | None = None,
) -> Render:
    now = now or utc_now()
    plan = store.renders.get_plan(plan_id)
    draft = store.editorial.get(plan.editorial_version_id)
    if draft.status is not DraftStatus.APPROVED:
        msg = f"draft {draft.id!r} is {draft.status.value}; rendering needs an approved draft"
        raise RenderError(msg)
    candidate = store.candidates.get(draft.candidate_id)
    policy = policy_for_candidate(store, candidate)
    require(policy, Action.CLIP, now=now)
    require(policy, Action.RENDER_TRANSFORM, now=now)
    transcript = store.transcripts.get(candidate.transcript_id)
    asset = store.media.get(transcript.media_asset_id)
    source = Path(asset.path_or_object_key)
    shown = display_path(source, artifacts_dir)
    if not source.is_file():
        msg = f"source media missing: {shown}"
        raise RenderError(msg)
    if sha256_file(source) != asset.sha256:
        msg = f"source media no longer matches its registered sha256: {shown}"
        raise RenderError(msg)

    render_id = new_id("rnd")
    out_dir = artifacts_dir / "renders"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{render_id}.mp4"
    workdir = artifacts_dir / "tmp" / render_id
    argv = build_command(
        plan,
        source,
        out_path,
        workdir,
        settings,
        has_video=asset.video_codec is not None,
        has_audio=asset.audio_codec is not None,
    )
    log.info(
        "render_started",
        extra={
            "render_plan_id": plan.id,
            "render_id": render_id,
            "source": shown,
            "items": len(plan.timeline),
            "expected_duration_ms": plan.total_duration_ms,
            "preset": settings.preset,
        },
    )
    try:
        run_ffmpeg(argv, settings.timeout_s)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    probe = probe_media(out_path, ffprobe_bin=settings.ffprobe_bin)
    validation = _validate(plan, probe)
    if not validation["passed"]:
        out_path.unlink(missing_ok=True)
        msg = f"rendered file failed validation: {'; '.join(validation['problems'])}"
        raise RenderError(msg)
    render = Render(
        id=render_id,
        render_plan_id=plan.id,
        file_path=str(out_path.resolve()),
        sha256=sha256_file(out_path),
        duration_ms=probe.duration_ms or 0,
        validation=validation,
        created_at=now,
    )
    with store.transaction():
        store.renders.add_render(render)
    log.info(
        "render_completed",
        extra={
            "render_id": render.id,
            "render_plan_id": plan.id,
            "file": display_path(out_path, artifacts_dir),
            "sha256": render.sha256,
            "duration_ms": render.duration_ms,
            "size_bytes": out_path.stat().st_size,
        },
    )
    return render
