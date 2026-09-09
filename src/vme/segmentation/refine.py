"""Refine existing candidates with the boundary editor, as derived (immutable) rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from vme.domain.models import Candidate, new_id, utc_now
from vme.logs import get_logger
from vme.rights.gate import Action, require
from vme.segmentation.boundary import BoundaryConfig, BoundaryEdit, refine_span
from vme.storage.db import Store
from vme.storage.repositories import NotFoundError

log = get_logger("segmentation.refine")


def created_by(config: BoundaryConfig) -> str:
    return f"boundary_editor:{config.version}"


@dataclass(frozen=True, slots=True)
class RefineResult:
    refined: list[Candidate]  # new derived candidates
    unchanged: int
    edits: dict[str, BoundaryEdit]  # original candidate id -> edit


def refine_candidates(
    store: Store,
    transcript_id: str,
    config: BoundaryConfig | None = None,
    *,
    source_created_by: str | None = None,
    now: datetime | None = None,
) -> RefineResult:
    """Create a ``boundary_editor`` candidate for every candidate whose span changes.

    Originals are untouched (ARCHITECTURE §4); rankings and labels keep pointing at them.
    Running twice is idempotent: a candidate that already has a derived row is skipped.
    """
    config = config or BoundaryConfig()
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

    words = transcript.words()
    existing = store.candidates.list(transcript_id)
    already = {c.derived_from_id for c in existing if c.derived_from_id}
    originals = [
        c
        for c in existing
        if c.derived_from_id is None
        and (source_created_by is None or c.created_by == source_created_by)
        and c.id not in already
    ]
    refined: list[Candidate] = []
    edits: dict[str, BoundaryEdit] = {}
    unchanged = 0
    for c in originals:
        edit = refine_span(words, c.start_ms, c.end_ms, config)
        if not edit.changed:
            unchanged += 1
            continue
        kept = [w for w in words if w.start_ms >= edit.start_ms and w.end_ms <= edit.end_ms]
        if not kept:
            unchanged += 1
            continue
        first = words.index(kept[0])
        after_start = first + len(kept)
        edits[c.id] = edit
        refined.append(
            Candidate(
                id=new_id("cnd"),
                transcript_id=transcript_id,
                start_ms=edit.start_ms,
                end_ms=max(edit.end_ms, edit.start_ms + 1),
                context_before=" ".join(w.text for w in words[max(0, first - 40) : first]),
                context_after=" ".join(w.text for w in words[after_start : after_start + 40]),
                speaker=c.speaker,
                topic=c.topic,
                candidate_text=" ".join(w.text for w in kept),
                created_by=created_by(config),
                derived_from_id=c.id,
                created_at=now,
            )
        )
    if refined:
        with store.transaction():
            store.candidates.add_many(refined)
    log.info(
        "candidates_refined",
        extra={
            "transcript_id": transcript_id,
            "created_by": created_by(config),
            "refined": len(refined),
            "unchanged": unchanged,
            "reasons": sorted({r for e in edits.values() for r in e.reasons}),
        },
    )
    return RefineResult(refined=refined, unchanged=unchanged, edits=edits)
