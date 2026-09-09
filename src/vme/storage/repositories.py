"""Small repository layer. Rows <-> Pydantic models; no business rules here."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from vme.domain.models import MediaAsset, RightsPolicy, Source, SourceStatus


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
        rows = self._conn.execute("SELECT * FROM sources ORDER BY created_at, id").fetchall()
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
            rows = self._conn.execute("SELECT * FROM media_assets ORDER BY ingested_at, id")
        else:
            rows = self._conn.execute(
                "SELECT * FROM media_assets WHERE source_id = ? ORDER BY ingested_at, id",
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


def _fail(column: str) -> Any:
    msg = f"corrupt row: {column} is NULL"
    raise ValueError(msg)
