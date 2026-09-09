"""Transcribe a registered media asset into an immutable ``RAW`` transcript."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from vme.domain.models import Transcript, TranscriptKind, new_id, utc_now
from vme.ingestion.fingerprint import sha256_file
from vme.logs import display_path, get_logger
from vme.rights.gate import Action, require
from vme.storage.db import Store
from vme.storage.repositories import NotFoundError
from vme.transcription.base import SpeechToText, TranscriptionError

log = get_logger("transcription")


def _policy_for_source(store: Store, source_id: str) -> object:
    source = store.sources.get(source_id)
    if source.rights_policy_id is None:
        return None
    try:
        return store.policies.get(source.rights_policy_id)
    except NotFoundError:
        return None  # dangling reference evaluates as no policy: fail closed


def transcribe_media(
    store: Store,
    media_asset_id: str,
    stt: SpeechToText,
    *,
    artifacts_dir: Path,
    now: datetime | None = None,
) -> Transcript:
    """Rights gate -> fingerprint check -> provider -> persisted ``RAW`` transcript.

    The gate is re-evaluated here (the policy may have expired since ingestion). The
    file must still hash to the stored fingerprint so the transcript is reproducible from
    the registered input.
    """
    now = now or utc_now()
    asset = store.media.get(media_asset_id)
    policy = _policy_for_source(store, asset.source_id)
    require(policy, Action.INGEST, now=now)  # type: ignore[arg-type]

    path = Path(asset.path_or_object_key)
    shown = display_path(path, artifacts_dir)
    if not path.is_file():
        msg = f"media file for {media_asset_id} is missing: {shown}"
        raise TranscriptionError(msg)
    if sha256_file(path) != asset.sha256:
        msg = f"media file for {media_asset_id} no longer matches its registered sha256: {shown}"
        raise TranscriptionError(msg)

    started = time.perf_counter()
    result = stt.transcribe(path)
    latency_ms = round((time.perf_counter() - started) * 1000)
    if not result.segments:
        msg = f"provider {result.provider} returned no segments for {shown}"
        raise TranscriptionError(msg)

    transcript = Transcript(
        id=new_id("trn"),
        media_asset_id=asset.id,
        kind=TranscriptKind.RAW,
        derived_from_id=None,
        version=store.transcripts.next_version(asset.id, TranscriptKind.RAW),
        provider=result.provider,
        provider_version=result.provider_version,
        model_alias=result.model_alias,
        language=result.language,
        raw_text=result.text,
        segments=result.segments,
        created_at=now,
    )
    with store.transaction():
        store.transcripts.add(transcript)
    log.info(
        "transcript_created",
        extra={
            "media_id": asset.id,
            "transcript_id": transcript.id,
            "version": transcript.version,
            "provider": transcript.provider,
            "provider_version": transcript.provider_version,
            "model_alias": transcript.model_alias,
            "language": transcript.language,
            "language_probability": result.language_probability,
            "parameters": result.parameters,
            "segments": len(transcript.segments),
            "words": len(transcript.words()),
            "duration_ms": transcript.duration_ms,
            "latency_ms": latency_ms,
        },
    )
    return transcript
