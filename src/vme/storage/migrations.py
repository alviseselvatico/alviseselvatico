"""Ordered, append-only schema migrations. Never edit an applied migration: add a new one."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="phase0_sources_policies_media",
        sql="""
        CREATE TABLE rights_policies (
            id                   TEXT PRIMARY KEY,
            basis_type           TEXT    NOT NULL,
            basis_reference      TEXT,
            can_ingest           INTEGER NOT NULL CHECK (can_ingest IN (0, 1)),
            can_extract_clip     INTEGER NOT NULL CHECK (can_extract_clip IN (0, 1)),
            can_transform        INTEGER NOT NULL CHECK (can_transform IN (0, 1)),
            can_publish          INTEGER NOT NULL CHECK (can_publish IN (0, 1)),
            requires_attribution INTEGER NOT NULL CHECK (requires_attribution IN (0, 1)),
            attribution_text     TEXT,
            territory_notes      TEXT,
            expiry_at            TEXT,
            review_required      INTEGER NOT NULL CHECK (review_required IN (0, 1)),
            notes                TEXT,
            created_at           TEXT    NOT NULL
        );

        CREATE TABLE sources (
            id               TEXT PRIMARY KEY,
            kind             TEXT NOT NULL,
            canonical_uri    TEXT NOT NULL,
            publisher        TEXT,
            title            TEXT,
            created_at       TEXT NOT NULL,
            rights_policy_id TEXT REFERENCES rights_policies(id),
            status           TEXT NOT NULL
        );

        CREATE TABLE media_assets (
            id                 TEXT PRIMARY KEY,
            source_id          TEXT    NOT NULL REFERENCES sources(id),
            sha256             TEXT    NOT NULL,
            path_or_object_key TEXT    NOT NULL,
            duration_ms        INTEGER,
            width              INTEGER,
            height             INTEGER,
            audio_codec        TEXT,
            video_codec        TEXT,
            ingested_at        TEXT    NOT NULL,
            UNIQUE (source_id, sha256)
        );
        CREATE INDEX media_assets_source_idx ON media_assets(source_id);
        """,
    ),
)
