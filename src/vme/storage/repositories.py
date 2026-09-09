"""Small repository layer. Rows <-> Pydantic models; no business rules here."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from pydantic import TypeAdapter

from vme.domain.models import (
    Benchmark,
    Candidate,
    CaptionConfig,
    Claim,
    ClaimStatus,
    DraftStatus,
    EditorialVersion,
    Evidence,
    ExcerptSpan,
    Label,
    LlmCall,
    MediaAsset,
    OverlayConfig,
    RankingBatch,
    RankingRun,
    Render,
    RenderPlan,
    ReviewEvent,
    RightsPolicy,
    Source,
    SourceStatus,
    TimelineItem,
    Transcript,
    TranscriptKind,
    TranscriptSegment,
)

_SEGMENTS = TypeAdapter(list[TranscriptSegment])
_STR_LIST = TypeAdapter(list[str])
_FLOATS = TypeAdapter(dict[str, float])
_JSON_OBJ = TypeAdapter(dict[str, Any])
_SPANS = TypeAdapter(list[ExcerptSpan])
_TIMELINE = TypeAdapter(list[TimelineItem])


class NotFoundError(LookupError):
    pass


class DuplicateRecordError(ValueError):
    pass


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class RightsPolicyRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, policy: RightsPolicy) -> RightsPolicy:
        try:
            self._conn.execute(
                """
                INSERT INTO rights_policies (
                    id, basis_type, basis_reference, can_ingest, can_extract_clip,
                    can_transform, can_publish, requires_attribution, attribution_text,
                    territory_notes, expiry_at, review_required, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    policy.id,
                    policy.basis_type.value,
                    policy.basis_reference,
                    int(policy.can_ingest),
                    int(policy.can_extract_clip),
                    int(policy.can_transform),
                    int(policy.can_publish),
                    int(policy.requires_attribution),
                    policy.attribution_text,
                    policy.territory_notes,
                    _iso(policy.expiry_at),
                    int(policy.review_required),
                    policy.notes,
                    _iso(policy.created_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            msg = f"rights policy {policy.id!r} already exists"
            raise DuplicateRecordError(msg) from exc
        return policy

    def get(self, policy_id: str) -> RightsPolicy:
        row = self._conn.execute(
            "SELECT * FROM rights_policies WHERE id = ?", (policy_id,)
        ).fetchone()
        if row is None:
            msg = f"rights policy {policy_id!r} not found"
            raise NotFoundError(msg)
        return self._to_model(row)

    @staticmethod
    def _to_model(row: sqlite3.Row) -> RightsPolicy:
        # Validation re-runs D009 on read: a hand-edited row cannot pass the gate either.
        return RightsPolicy(
            id=row["id"],
            basis_type=row["basis_type"],
            basis_reference=row["basis_reference"],
            can_ingest=bool(row["can_ingest"]),
            can_extract_clip=bool(row["can_extract_clip"]),
            can_transform=bool(row["can_transform"]),
            can_publish=bool(row["can_publish"]),
            requires_attribution=bool(row["requires_attribution"]),
            attribution_text=row["attribution_text"],
            territory_notes=row["territory_notes"],
            expiry_at=_dt(row["expiry_at"]),
            review_required=bool(row["review_required"]),
            notes=row["notes"],
            created_at=_dt(row["created_at"]) or _fail("created_at"),
        )


class SourceRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, source: Source) -> Source:
        try:
            self._conn.execute(
                """
                INSERT INTO sources (
                    id, kind, canonical_uri, publisher, title, created_at,
                    rights_policy_id, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source.id,
                    source.kind.value,
                    source.canonical_uri,
                    source.publisher,
                    source.title,
                    _iso(source.created_at),
                    source.rights_policy_id,
                    source.status.value,
                ),
            )
        except sqlite3.IntegrityError as exc:
            msg = f"source {source.id!r} already exists or references a missing policy"
            raise DuplicateRecordError(msg) from exc
        return source

    def get(self, source_id: str) -> Source:
        row = self._conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            msg = f"source {source_id!r} not found"
            raise NotFoundError(msg)
        return self._to_model(row)

    def list(self) -> list[Source]:
        rows = self._conn.execute("SELECT * FROM sources ORDER BY created_at, rowid").fetchall()
        return [self._to_model(r) for r in rows]

    def attach_policy(self, source_id: str, policy_id: str) -> Source:
        """Point the source at a (new) policy. Old policy rows are kept for audit."""
        cur = self._conn.execute(
            "UPDATE sources SET rights_policy_id = ? WHERE id = ?", (policy_id, source_id)
        )
        if cur.rowcount != 1:
            msg = f"source {source_id!r} not found"
            raise NotFoundError(msg)
        return self.get(source_id)

    def set_status(self, source_id: str, status: SourceStatus) -> Source:
        cur = self._conn.execute(
            "UPDATE sources SET status = ? WHERE id = ?", (status.value, source_id)
        )
        if cur.rowcount != 1:
            msg = f"source {source_id!r} not found"
            raise NotFoundError(msg)
        return self.get(source_id)

    @staticmethod
    def _to_model(row: sqlite3.Row) -> Source:
        return Source(
            id=row["id"],
            kind=row["kind"],
            canonical_uri=row["canonical_uri"],
            publisher=row["publisher"],
            title=row["title"],
            created_at=_dt(row["created_at"]) or _fail("created_at"),
            rights_policy_id=row["rights_policy_id"],
            status=row["status"],
        )


class MediaAssetRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, asset: MediaAsset) -> MediaAsset:
        try:
            self._conn.execute(
                """
                INSERT INTO media_assets (
                    id, source_id, sha256, path_or_object_key, duration_ms, width, height,
                    audio_codec, video_codec, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset.id,
                    asset.source_id,
                    asset.sha256,
                    asset.path_or_object_key,
                    asset.duration_ms,
                    asset.width,
                    asset.height,
                    asset.audio_codec,
                    asset.video_codec,
                    _iso(asset.ingested_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            msg = (
                f"media asset {asset.id!r} already exists, or sha256 {asset.sha256[:12]}… "
                f"is already registered for source {asset.source_id!r}"
            )
            raise DuplicateRecordError(msg) from exc
        return asset

    def get(self, asset_id: str) -> MediaAsset:
        row = self._conn.execute("SELECT * FROM media_assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            msg = f"media asset {asset_id!r} not found"
            raise NotFoundError(msg)
        return self._to_model(row)

    def find_by_fingerprint(self, source_id: str, sha256: str) -> MediaAsset | None:
        row = self._conn.execute(
            "SELECT * FROM media_assets WHERE source_id = ? AND sha256 = ?", (source_id, sha256)
        ).fetchone()
        return self._to_model(row) if row is not None else None

    def list(self, source_id: str | None = None) -> list[MediaAsset]:
        if source_id is None:
            rows = self._conn.execute("SELECT * FROM media_assets ORDER BY ingested_at, rowid")
        else:
            rows = self._conn.execute(
                "SELECT * FROM media_assets WHERE source_id = ? ORDER BY ingested_at, rowid",
                (source_id,),
            )
        return [self._to_model(r) for r in rows.fetchall()]

    @staticmethod
    def _to_model(row: sqlite3.Row) -> MediaAsset:
        return MediaAsset(
            id=row["id"],
            source_id=row["source_id"],
            sha256=row["sha256"],
            path_or_object_key=row["path_or_object_key"],
            duration_ms=row["duration_ms"],
            width=row["width"],
            height=row["height"],
            audio_codec=row["audio_codec"],
            video_codec=row["video_codec"],
            ingested_at=_dt(row["ingested_at"]) or _fail("ingested_at"),
        )


class TranscriptRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def next_version(self, media_asset_id: str, kind: TranscriptKind) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM transcripts "
            "WHERE media_asset_id = ? AND kind = ?",
            (media_asset_id, kind.value),
        ).fetchone()
        return int(row[0]) + 1

    def add(self, transcript: Transcript) -> Transcript:
        try:
            self._conn.execute(
                """
                INSERT INTO transcripts (
                    id, media_asset_id, kind, derived_from_id, version, provider,
                    provider_version, model_alias, language, raw_text, segments_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transcript.id,
                    transcript.media_asset_id,
                    transcript.kind.value,
                    transcript.derived_from_id,
                    transcript.version,
                    transcript.provider,
                    transcript.provider_version,
                    transcript.model_alias,
                    transcript.language,
                    transcript.raw_text,
                    _SEGMENTS.dump_json(transcript.segments).decode("utf-8"),
                    _iso(transcript.created_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            msg = (
                f"transcript {transcript.id!r} already exists, version "
                f"{transcript.version} taken, or media/parent reference missing"
            )
            raise DuplicateRecordError(msg) from exc
        return transcript

    def get(self, transcript_id: str) -> Transcript:
        row = self._conn.execute(
            "SELECT * FROM transcripts WHERE id = ?", (transcript_id,)
        ).fetchone()
        if row is None:
            msg = f"transcript {transcript_id!r} not found"
            raise NotFoundError(msg)
        return self._to_model(row)

    def list(self, media_asset_id: str | None = None) -> list[Transcript]:
        if media_asset_id is None:
            rows = self._conn.execute("SELECT * FROM transcripts ORDER BY created_at, rowid")
        else:
            rows = self._conn.execute(
                "SELECT * FROM transcripts WHERE media_asset_id = ? ORDER BY kind, version",
                (media_asset_id,),
            )
        return [self._to_model(r) for r in rows.fetchall()]

    @staticmethod
    def _to_model(row: sqlite3.Row) -> Transcript:
        return Transcript(
            id=row["id"],
            media_asset_id=row["media_asset_id"],
            kind=row["kind"],
            derived_from_id=row["derived_from_id"],
            version=row["version"],
            provider=row["provider"],
            provider_version=row["provider_version"],
            model_alias=row["model_alias"],
            language=row["language"],
            raw_text=row["raw_text"],
            segments=_SEGMENTS.validate_json(row["segments_json"]),
            created_at=_dt(row["created_at"]) or _fail("created_at"),
        )


class CandidateRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add_many(self, candidates: list[Candidate]) -> list[Candidate]:
        try:
            self._conn.executemany(
                """
                INSERT INTO candidates (
                    id, transcript_id, start_ms, end_ms, context_before, context_after,
                    speaker, topic, candidate_text, created_by, derived_from_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        c.id,
                        c.transcript_id,
                        c.start_ms,
                        c.end_ms,
                        c.context_before,
                        c.context_after,
                        c.speaker,
                        c.topic,
                        c.candidate_text,
                        c.created_by,
                        c.derived_from_id,
                        _iso(c.created_at),
                    )
                    for c in candidates
                ],
            )
        except sqlite3.IntegrityError as exc:
            msg = "candidate id already exists or transcript reference missing"
            raise DuplicateRecordError(msg) from exc
        return candidates

    def get(self, candidate_id: str) -> Candidate:
        row = self._conn.execute(
            "SELECT * FROM candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        if row is None:
            msg = f"candidate {candidate_id!r} not found"
            raise NotFoundError(msg)
        return self._to_model(row)

    def list(self, transcript_id: str, created_by: str | None = None) -> list[Candidate]:
        if created_by is None:
            rows = self._conn.execute(
                "SELECT * FROM candidates WHERE transcript_id = ? ORDER BY start_ms, id",
                (transcript_id,),
            )
        else:
            rows = self._conn.execute(
                "SELECT * FROM candidates WHERE transcript_id = ? AND created_by = ? "
                "ORDER BY start_ms, id",
                (transcript_id, created_by),
            )
        return [self._to_model(r) for r in rows.fetchall()]

    @staticmethod
    def _to_model(row: sqlite3.Row) -> Candidate:
        return Candidate(
            id=row["id"],
            transcript_id=row["transcript_id"],
            start_ms=row["start_ms"],
            end_ms=row["end_ms"],
            context_before=row["context_before"],
            context_after=row["context_after"],
            speaker=row["speaker"],
            topic=row["topic"],
            candidate_text=row["candidate_text"],
            created_by=row["created_by"],
            derived_from_id=row["derived_from_id"],
            created_at=_dt(row["created_at"]) or _fail("created_at"),
        )


class LlmCallRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, call: LlmCall) -> LlmCall:
        try:
            self._conn.execute(
                """
                INSERT INTO llm_calls (
                    id, purpose, input_artifact_refs_json, prompt_name, prompt_version,
                    provider, model_alias, model_id_reported, parameters_json, response_json,
                    validation_status, attempts, latency_ms, input_tokens, output_tokens,
                    estimated_cost_usd, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call.id,
                    call.purpose,
                    json.dumps(call.input_artifact_refs),
                    call.prompt_name,
                    call.prompt_version,
                    call.provider,
                    call.model_alias,
                    call.model_id_reported,
                    json.dumps(call.parameters, sort_keys=True, default=str),
                    json.dumps(call.response, sort_keys=True, default=str)
                    if call.response is not None
                    else None,
                    call.validation_status.value,
                    call.attempts,
                    call.latency_ms,
                    call.input_tokens,
                    call.output_tokens,
                    call.estimated_cost_usd,
                    call.error,
                    _iso(call.created_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            msg = f"llm call {call.id!r} already exists"
            raise DuplicateRecordError(msg) from exc
        return call

    def get(self, call_id: str) -> LlmCall:
        row = self._conn.execute("SELECT * FROM llm_calls WHERE id = ?", (call_id,)).fetchone()
        if row is None:
            msg = f"llm call {call_id!r} not found"
            raise NotFoundError(msg)
        return self._to_model(row)

    def list(self, purpose: str | None = None) -> list[LlmCall]:
        if purpose is None:
            rows = self._conn.execute("SELECT * FROM llm_calls ORDER BY created_at, rowid")
        else:
            rows = self._conn.execute(
                "SELECT * FROM llm_calls WHERE purpose = ? ORDER BY created_at, rowid", (purpose,)
            )
        return [self._to_model(r) for r in rows.fetchall()]

    @staticmethod
    def _to_model(row: sqlite3.Row) -> LlmCall:
        return LlmCall(
            id=row["id"],
            purpose=row["purpose"],
            input_artifact_refs=_STR_LIST.validate_json(row["input_artifact_refs_json"]),
            prompt_name=row["prompt_name"],
            prompt_version=row["prompt_version"],
            provider=row["provider"],
            model_alias=row["model_alias"],
            model_id_reported=row["model_id_reported"],
            parameters=_JSON_OBJ.validate_json(row["parameters_json"]),
            response=_JSON_OBJ.validate_json(row["response_json"])
            if row["response_json"] is not None
            else None,
            validation_status=row["validation_status"],
            attempts=row["attempts"],
            latency_ms=row["latency_ms"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            estimated_cost_usd=row["estimated_cost_usd"],
            error=row["error"],
            created_at=_dt(row["created_at"]) or _fail("created_at"),
        )


class RankingRepository:
    """Batches and their runs (immutable once written)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add_batch(self, batch: RankingBatch) -> RankingBatch:
        try:
            self._conn.execute(
                """
                INSERT INTO ranking_batches (
                    id, transcript_id, scoring_version, weights_version, prompt_version,
                    model_alias, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.id,
                    batch.transcript_id,
                    batch.scoring_version,
                    batch.weights_version,
                    batch.prompt_version,
                    batch.model_alias,
                    _iso(batch.created_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            msg = f"ranking batch {batch.id!r} already exists or transcript missing"
            raise DuplicateRecordError(msg) from exc
        return batch

    def get_batch(self, batch_id: str) -> RankingBatch:
        row = self._conn.execute(
            "SELECT * FROM ranking_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if row is None:
            msg = f"ranking batch {batch_id!r} not found"
            raise NotFoundError(msg)
        return self._batch(row)

    def list_batches(self, transcript_id: str | None = None) -> list[RankingBatch]:
        if transcript_id is None:
            rows = self._conn.execute("SELECT * FROM ranking_batches ORDER BY created_at, rowid")
        else:
            rows = self._conn.execute(
                "SELECT * FROM ranking_batches WHERE transcript_id = ? ORDER BY created_at, rowid",
                (transcript_id,),
            )
        return [self._batch(r) for r in rows.fetchall()]

    def add_runs(self, runs: list[RankingRun]) -> list[RankingRun]:
        try:
            self._conn.executemany(
                """
                INSERT INTO ranking_runs (
                    id, ranking_batch_id, candidate_id, tier, feature_json, risk_json,
                    final_score, rationale, llm_call_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r.id,
                        r.ranking_batch_id,
                        r.candidate_id,
                        r.tier.value,
                        json.dumps(r.features, sort_keys=True),
                        json.dumps(r.risks, sort_keys=True),
                        r.final_score,
                        r.rationale,
                        r.llm_call_id,
                        _iso(r.created_at),
                    )
                    for r in runs
                ],
            )
        except sqlite3.IntegrityError as exc:
            msg = "ranking run duplicate, or batch/candidate/llm_call reference missing"
            raise DuplicateRecordError(msg) from exc
        return runs

    def list_runs(self, batch_id: str) -> list[RankingRun]:
        rows = self._conn.execute(
            "SELECT * FROM ranking_runs WHERE ranking_batch_id = ? ORDER BY "
            "CASE tier WHEN 'strong' THEN 0 WHEN 'cheap' THEN 1 ELSE 2 END, "
            "final_score DESC, candidate_id",
            (batch_id,),
        )
        return [self._run(r) for r in rows.fetchall()]

    @staticmethod
    def _batch(row: sqlite3.Row) -> RankingBatch:
        return RankingBatch(
            id=row["id"],
            transcript_id=row["transcript_id"],
            scoring_version=row["scoring_version"],
            weights_version=row["weights_version"],
            prompt_version=row["prompt_version"],
            model_alias=row["model_alias"],
            created_at=_dt(row["created_at"]) or _fail("created_at"),
        )

    @staticmethod
    def _run(row: sqlite3.Row) -> RankingRun:
        return RankingRun(
            id=row["id"],
            ranking_batch_id=row["ranking_batch_id"],
            candidate_id=row["candidate_id"],
            tier=row["tier"],
            features=_FLOATS.validate_json(row["feature_json"]),
            risks=_FLOATS.validate_json(row["risk_json"]),
            final_score=row["final_score"],
            rationale=row["rationale"],
            llm_call_id=row["llm_call_id"],
            created_at=_dt(row["created_at"]) or _fail("created_at"),
        )


class EditorialRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def next_version(self, candidate_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM editorial_versions WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        return int(row[0]) + 1

    def add(self, draft: EditorialVersion) -> EditorialVersion:
        try:
            self._conn.execute(
                """
                INSERT INTO editorial_versions (
                    id, candidate_id, version, hook, commentary_before, commentary_after,
                    excerpt_plan_json, title, title_options_json, cta, transformation_summary,
                    status, prompt_version, model_alias, llm_call_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.id,
                    draft.candidate_id,
                    draft.version,
                    draft.hook,
                    draft.commentary_before,
                    draft.commentary_after,
                    _SPANS.dump_json(draft.excerpt_plan).decode("utf-8"),
                    draft.title,
                    json.dumps(draft.title_options),
                    draft.cta,
                    draft.transformation_summary,
                    draft.status.value,
                    draft.prompt_version,
                    draft.model_alias,
                    draft.llm_call_id,
                    _iso(draft.created_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            msg = f"editorial version {draft.id!r} duplicate or candidate/llm_call missing"
            raise DuplicateRecordError(msg) from exc
        return draft

    def get(self, draft_id: str) -> EditorialVersion:
        row = self._conn.execute(
            "SELECT * FROM editorial_versions WHERE id = ?", (draft_id,)
        ).fetchone()
        if row is None:
            msg = f"editorial version {draft_id!r} not found"
            raise NotFoundError(msg)
        return self._to_model(row)

    def list(self, candidate_id: str | None = None) -> list[EditorialVersion]:
        if candidate_id is None:
            rows = self._conn.execute("SELECT * FROM editorial_versions ORDER BY created_at, rowid")
        else:
            rows = self._conn.execute(
                "SELECT * FROM editorial_versions WHERE candidate_id = ? ORDER BY version",
                (candidate_id,),
            )
        return [self._to_model(r) for r in rows.fetchall()]

    def set_status(self, draft_id: str, status: DraftStatus) -> EditorialVersion:
        cur = self._conn.execute(
            "UPDATE editorial_versions SET status = ? WHERE id = ?", (status.value, draft_id)
        )
        if cur.rowcount != 1:
            msg = f"editorial version {draft_id!r} not found"
            raise NotFoundError(msg)
        return self.get(draft_id)

    @staticmethod
    def _to_model(row: sqlite3.Row) -> EditorialVersion:
        return EditorialVersion(
            id=row["id"],
            candidate_id=row["candidate_id"],
            version=row["version"],
            hook=row["hook"],
            commentary_before=row["commentary_before"],
            commentary_after=row["commentary_after"],
            excerpt_plan=_SPANS.validate_json(row["excerpt_plan_json"]),
            title=row["title"],
            title_options=_STR_LIST.validate_json(row["title_options_json"]),
            cta=row["cta"],
            transformation_summary=row["transformation_summary"],
            status=row["status"],
            prompt_version=row["prompt_version"],
            model_alias=row["model_alias"],
            llm_call_id=row["llm_call_id"],
            created_at=_dt(row["created_at"]) or _fail("created_at"),
        )


class ClaimRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add_many(self, claims: list[Claim]) -> list[Claim]:
        try:
            self._conn.executemany(
                """
                INSERT INTO claims (
                    id, editorial_version_id, claim_text, claim_type, importance,
                    evidence_refs_json, confidence, status, reviewer_note, origin, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        c.id,
                        c.editorial_version_id,
                        c.claim_text,
                        c.claim_type.value,
                        c.importance.value,
                        json.dumps(c.evidence_refs),
                        c.confidence,
                        c.status.value,
                        c.reviewer_note,
                        c.origin,
                        _iso(c.created_at),
                    )
                    for c in claims
                ],
            )
        except sqlite3.IntegrityError as exc:
            msg = "claim duplicate or editorial version missing"
            raise DuplicateRecordError(msg) from exc
        return claims

    def get(self, claim_id: str) -> Claim:
        row = self._conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
        if row is None:
            msg = f"claim {claim_id!r} not found"
            raise NotFoundError(msg)
        return self._to_model(row)

    def list(self, editorial_version_id: str) -> list[Claim]:
        rows = self._conn.execute(
            "SELECT * FROM claims WHERE editorial_version_id = ? ORDER BY created_at, rowid",
            (editorial_version_id,),
        )
        return [self._to_model(r) for r in rows.fetchall()]

    def set_status(self, claim_id: str, status: ClaimStatus, note: str | None) -> Claim:
        cur = self._conn.execute(
            "UPDATE claims SET status = ?, reviewer_note = ? WHERE id = ?",
            (status.value, note, claim_id),
        )
        if cur.rowcount != 1:
            msg = f"claim {claim_id!r} not found"
            raise NotFoundError(msg)
        return self.get(claim_id)

    def set_machine_verdict(
        self,
        claim_id: str,
        *,
        status: ClaimStatus,
        machine_status: str,
        machine_reason: str,
        evaluated_at: datetime,
        confidence: float | None,
        note: str | None,
    ) -> Claim:
        cur = self._conn.execute(
            "UPDATE claims SET status = ?, machine_status = ?, machine_reason = ?, "
            "evaluated_at = ?, confidence = ?, reviewer_note = COALESCE(?, reviewer_note) "
            "WHERE id = ?",
            (
                status.value,
                machine_status,
                machine_reason,
                _iso(evaluated_at),
                confidence,
                note,
                claim_id,
            ),
        )
        if cur.rowcount != 1:
            msg = f"claim {claim_id!r} not found"
            raise NotFoundError(msg)
        return self.get(claim_id)

    @staticmethod
    def _to_model(row: sqlite3.Row) -> Claim:
        return Claim(
            id=row["id"],
            editorial_version_id=row["editorial_version_id"],
            claim_text=row["claim_text"],
            claim_type=row["claim_type"],
            importance=row["importance"],
            evidence_refs=_STR_LIST.validate_json(row["evidence_refs_json"]),
            confidence=row["confidence"],
            status=row["status"],
            reviewer_note=row["reviewer_note"],
            origin=row["origin"],
            machine_status=row["machine_status"],
            machine_reason=row["machine_reason"],
            evaluated_at=_dt(row["evaluated_at"]),
            created_at=_dt(row["created_at"]) or _fail("created_at"),
        )


class EvidenceRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add_many(self, items: list[Evidence]) -> list[Evidence]:
        try:
            self._conn.executemany(
                """
                INSERT INTO evidence (
                    id, claim_id, retriever, url, title, snippet, published, retrieved_at,
                    llm_call_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        e.id,
                        e.claim_id,
                        e.retriever,
                        e.url,
                        e.title,
                        e.snippet,
                        e.published,
                        _iso(e.retrieved_at),
                        e.llm_call_id,
                    )
                    for e in items
                ],
            )
        except sqlite3.IntegrityError as exc:
            msg = "evidence duplicate or claim/llm_call missing"
            raise DuplicateRecordError(msg) from exc
        return items

    def list(self, claim_id: str) -> list[Evidence]:
        rows = self._conn.execute(
            "SELECT * FROM evidence WHERE claim_id = ? ORDER BY retrieved_at, rowid", (claim_id,)
        )
        return [
            Evidence(
                id=r["id"],
                claim_id=r["claim_id"],
                retriever=r["retriever"],
                url=r["url"],
                title=r["title"],
                snippet=r["snippet"],
                published=r["published"],
                retrieved_at=_dt(r["retrieved_at"]) or _fail("retrieved_at"),
                llm_call_id=r["llm_call_id"],
            )
            for r in rows.fetchall()
        ]


class ReviewEventRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, event: ReviewEvent) -> ReviewEvent:
        try:
            self._conn.execute(
                """
                INSERT INTO review_events (
                    id, object_type, object_id, decision, reason_codes_json, notes, reviewer,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.object_type,
                    event.object_id,
                    event.decision,
                    json.dumps(event.reason_codes),
                    event.notes,
                    event.reviewer,
                    _iso(event.created_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            msg = f"review event {event.id!r} already exists"
            raise DuplicateRecordError(msg) from exc
        return event

    def list(
        self, object_type: str | None = None, object_id: str | None = None
    ) -> list[ReviewEvent]:
        if object_id is None:
            rows = self._conn.execute("SELECT * FROM review_events ORDER BY created_at, rowid")
        else:
            rows = self._conn.execute(
                "SELECT * FROM review_events WHERE object_type = ? AND object_id = ? "
                "ORDER BY created_at, rowid",
                (object_type, object_id),
            )
        return [self._to_model(r) for r in rows.fetchall()]

    @staticmethod
    def _to_model(row: sqlite3.Row) -> ReviewEvent:
        return ReviewEvent(
            id=row["id"],
            object_type=row["object_type"],
            object_id=row["object_id"],
            decision=row["decision"],
            reason_codes=_STR_LIST.validate_json(row["reason_codes_json"]),
            notes=row["notes"],
            reviewer=row["reviewer"],
            created_at=_dt(row["created_at"]) or _fail("created_at"),
        )


class RenderRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add_plan(self, plan: RenderPlan) -> RenderPlan:
        try:
            self._conn.execute(
                """
                INSERT INTO render_plans (
                    id, editorial_version_id, template_version, width, height, timeline_json,
                    caption_config_json, overlay_config_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.id,
                    plan.editorial_version_id,
                    plan.template_version,
                    plan.width,
                    plan.height,
                    _TIMELINE.dump_json(plan.timeline).decode("utf-8"),
                    plan.caption_config.model_dump_json(),
                    plan.overlay_config.model_dump_json(),
                    _iso(plan.created_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            msg = f"render plan {plan.id!r} duplicate or editorial version missing"
            raise DuplicateRecordError(msg) from exc
        return plan

    def get_plan(self, plan_id: str) -> RenderPlan:
        row = self._conn.execute("SELECT * FROM render_plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            msg = f"render plan {plan_id!r} not found"
            raise NotFoundError(msg)
        return self._plan(row)

    def list_plans(self, editorial_version_id: str | None = None) -> list[RenderPlan]:
        if editorial_version_id is None:
            rows = self._conn.execute("SELECT * FROM render_plans ORDER BY created_at, rowid")
        else:
            rows = self._conn.execute(
                "SELECT * FROM render_plans WHERE editorial_version_id = ? "
                "ORDER BY created_at, rowid",
                (editorial_version_id,),
            )
        return [self._plan(r) for r in rows.fetchall()]

    def add_render(self, render: Render) -> Render:
        try:
            self._conn.execute(
                """
                INSERT INTO renders (
                    id, render_plan_id, file_path, sha256, duration_ms, validation_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    render.id,
                    render.render_plan_id,
                    render.file_path,
                    render.sha256,
                    render.duration_ms,
                    json.dumps(render.validation, sort_keys=True, default=str),
                    _iso(render.created_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            msg = f"render {render.id!r} duplicate or plan missing"
            raise DuplicateRecordError(msg) from exc
        return render

    def get_render(self, render_id: str) -> Render:
        row = self._conn.execute("SELECT * FROM renders WHERE id = ?", (render_id,)).fetchone()
        if row is None:
            msg = f"render {render_id!r} not found"
            raise NotFoundError(msg)
        return self._render(row)

    def list_renders(self, plan_id: str | None = None) -> list[Render]:
        if plan_id is None:
            rows = self._conn.execute("SELECT * FROM renders ORDER BY created_at, rowid")
        else:
            rows = self._conn.execute(
                "SELECT * FROM renders WHERE render_plan_id = ? ORDER BY created_at, rowid",
                (plan_id,),
            )
        return [self._render(r) for r in rows.fetchall()]

    @staticmethod
    def _plan(row: sqlite3.Row) -> RenderPlan:
        return RenderPlan(
            id=row["id"],
            editorial_version_id=row["editorial_version_id"],
            template_version=row["template_version"],
            width=row["width"],
            height=row["height"],
            timeline=_TIMELINE.validate_json(row["timeline_json"]),
            caption_config=CaptionConfig.model_validate_json(row["caption_config_json"]),
            overlay_config=OverlayConfig.model_validate_json(row["overlay_config_json"]),
            created_at=_dt(row["created_at"]) or _fail("created_at"),
        )

    @staticmethod
    def _render(row: sqlite3.Row) -> Render:
        return Render(
            id=row["id"],
            render_plan_id=row["render_plan_id"],
            file_path=row["file_path"],
            sha256=row["sha256"],
            duration_ms=row["duration_ms"],
            validation=_JSON_OBJ.validate_json(row["validation_json"]),
            created_at=_dt(row["created_at"]) or _fail("created_at"),
        )


class LabelRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, label: Label) -> Label:
        try:
            self._conn.execute(
                """
                INSERT INTO labels (
                    id, candidate_id, reviewer, decision, boundary_correct, hook_quality,
                    factual_risk, rights_risk, rejection_reasons_json, expected_performance,
                    edited_text, notes, taxonomy_version, provisional, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    label.id,
                    label.candidate_id,
                    label.reviewer,
                    label.decision.value,
                    None if label.boundary_correct is None else int(label.boundary_correct),
                    label.hook_quality,
                    label.factual_risk,
                    label.rights_risk,
                    json.dumps(label.rejection_reasons),
                    label.expected_performance.value if label.expected_performance else None,
                    label.edited_text,
                    label.notes,
                    label.taxonomy_version,
                    int(label.provisional),
                    _iso(label.created_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            msg = f"label {label.id!r} duplicate or candidate missing"
            raise DuplicateRecordError(msg) from exc
        return label

    def get(self, label_id: str) -> Label:
        row = self._conn.execute("SELECT * FROM labels WHERE id = ?", (label_id,)).fetchone()
        if row is None:
            msg = f"label {label_id!r} not found"
            raise NotFoundError(msg)
        return self._to_model(row)

    def list(
        self, candidate_id: str | None = None, transcript_id: str | None = None
    ) -> list[Label]:
        if candidate_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM labels WHERE candidate_id = ? ORDER BY created_at, rowid",
                (candidate_id,),
            )
        elif transcript_id is not None:
            rows = self._conn.execute(
                "SELECT l.* FROM labels l JOIN candidates c ON c.id = l.candidate_id "
                "WHERE c.transcript_id = ? ORDER BY l.created_at, l.rowid",
                (transcript_id,),
            )
        else:
            rows = self._conn.execute("SELECT * FROM labels ORDER BY created_at, rowid")
        return [self._to_model(r) for r in rows.fetchall()]

    @staticmethod
    def _to_model(row: sqlite3.Row) -> Label:
        return Label(
            id=row["id"],
            candidate_id=row["candidate_id"],
            reviewer=row["reviewer"],
            decision=row["decision"],
            boundary_correct=None
            if row["boundary_correct"] is None
            else bool(row["boundary_correct"]),
            hook_quality=row["hook_quality"],
            factual_risk=row["factual_risk"],
            rights_risk=row["rights_risk"],
            rejection_reasons=_STR_LIST.validate_json(row["rejection_reasons_json"]),
            expected_performance=row["expected_performance"],
            edited_text=row["edited_text"],
            notes=row["notes"],
            taxonomy_version=row["taxonomy_version"],
            provisional=bool(row["provisional"]),
            created_at=_dt(row["created_at"]) or _fail("created_at"),
        )


class BenchmarkRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, bench: Benchmark) -> Benchmark:
        try:
            self._conn.execute(
                """
                INSERT INTO benchmarks (
                    id, ranking_batch_id, transcript_id, scoring_version, weights_version,
                    prompt_version, model_alias, n_labeled, metrics_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bench.id,
                    bench.ranking_batch_id,
                    bench.transcript_id,
                    bench.scoring_version,
                    bench.weights_version,
                    bench.prompt_version,
                    bench.model_alias,
                    bench.n_labeled,
                    json.dumps(bench.metrics, sort_keys=True, default=str),
                    _iso(bench.created_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            msg = f"benchmark {bench.id!r} duplicate or batch/transcript missing"
            raise DuplicateRecordError(msg) from exc
        return bench

    def get(self, bench_id: str) -> Benchmark:
        row = self._conn.execute("SELECT * FROM benchmarks WHERE id = ?", (bench_id,)).fetchone()
        if row is None:
            msg = f"benchmark {bench_id!r} not found"
            raise NotFoundError(msg)
        return self._to_model(row)

    def list(self, transcript_id: str | None = None) -> list[Benchmark]:
        if transcript_id is None:
            rows = self._conn.execute("SELECT * FROM benchmarks ORDER BY created_at, rowid")
        else:
            rows = self._conn.execute(
                "SELECT * FROM benchmarks WHERE transcript_id = ? ORDER BY created_at, rowid",
                (transcript_id,),
            )
        return [self._to_model(r) for r in rows.fetchall()]

    @staticmethod
    def _to_model(row: sqlite3.Row) -> Benchmark:
        return Benchmark(
            id=row["id"],
            ranking_batch_id=row["ranking_batch_id"],
            transcript_id=row["transcript_id"],
            scoring_version=row["scoring_version"],
            weights_version=row["weights_version"],
            prompt_version=row["prompt_version"],
            model_alias=row["model_alias"],
            n_labeled=row["n_labeled"],
            metrics=_JSON_OBJ.validate_json(row["metrics_json"]),
            created_at=_dt(row["created_at"]) or _fail("created_at"),
        )


def _fail(column: str) -> Any:
    msg = f"corrupt row: {column} is NULL"
    raise ValueError(msg)
