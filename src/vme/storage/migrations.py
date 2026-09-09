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
    Migration(
        version=2,
        name="phase0_transcripts_candidates",
        sql="""
        CREATE TABLE transcripts (
            id               TEXT PRIMARY KEY,
            media_asset_id   TEXT    NOT NULL REFERENCES media_assets(id),
            kind             TEXT    NOT NULL,
            derived_from_id  TEXT    REFERENCES transcripts(id),
            version          INTEGER NOT NULL,
            provider         TEXT    NOT NULL,
            provider_version TEXT    NOT NULL,
            model_alias      TEXT    NOT NULL,
            language         TEXT,
            raw_text         TEXT    NOT NULL,
            segments_json    TEXT    NOT NULL,
            created_at       TEXT    NOT NULL,
            UNIQUE (media_asset_id, kind, version)
        );

        CREATE TABLE candidates (
            id             TEXT    PRIMARY KEY,
            transcript_id  TEXT    NOT NULL REFERENCES transcripts(id),
            start_ms       INTEGER NOT NULL,
            end_ms         INTEGER NOT NULL,
            context_before TEXT    NOT NULL,
            context_after  TEXT    NOT NULL,
            speaker        TEXT,
            topic          TEXT,
            candidate_text TEXT    NOT NULL,
            created_by     TEXT    NOT NULL,
            created_at     TEXT    NOT NULL,
            CHECK (end_ms > start_ms)
        );
        CREATE INDEX candidates_transcript_idx ON candidates(transcript_id, start_ms);
        """,
    ),
)
