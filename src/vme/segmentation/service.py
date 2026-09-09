"""Persist segmenter output for a transcript, behind the rights gate."""

from __future__ import annotations

from datetime import datetime

from vme.domain.models import Candidate, utc_now
from vme.logs import get_logger
from vme.rights.gate import Action, require
from vme.segmentation.segmenter import SegmentationConfig, created_by, segment_transcript
from vme.storage.db import Store
from vme.storage.repositories import DuplicateRecordError, NotFoundError

log = get_logger("segmentation")


def segment_and_store(
    store: Store,
    transcript_id: str,
    config: SegmentationConfig | None = None,
    *,
    now: datetime | None = None,
) -> list[Candidate]:
    """Candidates are immutable: one run per (transcript, segmenter version)."""
    config = config or SegmentationConfig()
    now = now or utc_now()
    transcript = store.transcripts.get(transcript_id)
    asset = store.media.get(transcript.media_asset_id)
    source = store.sources.get(asset.source_id)
    policy = None
    if source.rights_policy_id is not None:
        try:
            policy = store.policies.get(source.rights_policy_id)
        except NotFoundError:
            policy = None
    require(policy, Action.INGEST, now=now)

    who = created_by(config)
    if store.candidates.list(transcript_id, created_by=who):
        msg = f"candidates by {who!r} already exist for transcript {transcript_id!r}"
        raise DuplicateRecordError(msg)
    candidates = segment_transcript(transcript, config, now=now)
    with store.transaction():
        store.candidates.add_many(candidates)
    log.info(
        "candidates_created",
        extra={
            "transcript_id": transcript_id,
            "created_by": who,
            "config": {
                "min_ms": config.min_ms,
                "target_ms": config.target_ms,
                "max_ms": config.max_ms,
                "sentence_pause_ms": config.sentence_pause_ms,
                "hard_pause_ms": config.hard_pause_ms,
                "context_words": config.context_words,
            },
            "count": len(candidates),
            "durations_ms": [c.duration_ms for c in candidates],
        },
    )
    return candidates
