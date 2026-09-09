"""Register a local media file for a source, behind the rights gate."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from vme.domain.models import MediaAsset, SourceStatus, new_id, utc_now
from vme.ingestion.fingerprint import sha256_file
from vme.ingestion.probe import probe_media
from vme.logs import display_path, get_logger
from vme.rights.gate import Action, require
from vme.storage.db import Store
from vme.storage.repositories import DuplicateRecordError, NotFoundError

log = get_logger("ingestion")


class IngestionError(RuntimeError):
    pass


def register_local_media(
    store: Store,
    source_id: str,
    path: Path,
    *,
    artifacts_dir: Path,
    ffprobe_bin: str = "ffprobe",
    now: datetime | None = None,
) -> MediaAsset:
    """Fingerprint, probe and persist ``path`` as a :class:`MediaAsset` of ``source_id``.

    Order matters: the rights gate runs *before* any byte of the file is read.
    Raises :class:`vme.rights.RightsBlockedError` when the policy does not permit
    ``ingest``; nothing is written in that case.
    """
    now = now or utc_now()
    shown = display_path(path, artifacts_dir)
    source = store.sources.get(source_id)
    policy = None
    if source.rights_policy_id is not None:
        try:
            policy = store.policies.get(source.rights_policy_id)
        except NotFoundError as exc:  # dangling reference: fail closed
            msg = f"source {source_id!r} references missing policy {source.rights_policy_id!r}"
            raise IngestionError(msg) from exc
    decision = require(policy, Action.INGEST, now=now)
    log.info(
        "rights_gate_passed",
        extra={
            "source_id": source_id,
            "policy_id": decision.policy_id,
            "action": decision.action.value,
            "review_required": decision.review_required,
        },
    )

    if not path.is_file():
        msg = f"media file not found or not a regular file: {shown}"
        raise IngestionError(msg)
    fingerprint = sha256_file(path)
    existing = store.media.find_by_fingerprint(source_id, fingerprint)
    if existing is not None:
        msg = f"file already registered for source {source_id!r} as {existing.id}"
        raise DuplicateRecordError(msg)
    probe = probe_media(path, ffprobe_bin=ffprobe_bin)
    asset = MediaAsset(
        id=new_id("med"),
        source_id=source_id,
        sha256=fingerprint,
        path_or_object_key=str(path.resolve()),
        duration_ms=probe.duration_ms,
        width=probe.width,
        height=probe.height,
        audio_codec=probe.audio_codec,
        video_codec=probe.video_codec,
        ingested_at=now,
    )
    with store.transaction():
        store.media.add(asset)
        store.sources.set_status(source_id, SourceStatus.INGESTED)
    log.info(
        "media_registered",
        extra={
            "source_id": source_id,
            "media_id": asset.id,
            "sha256": asset.sha256,
            "file": shown,
            "duration_ms": asset.duration_ms,
            "width": asset.width,
            "height": asset.height,
            "audio_codec": asset.audio_codec,
            "video_codec": asset.video_codec,
        },
    )
    return asset
