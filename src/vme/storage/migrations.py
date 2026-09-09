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
    Migration(
        version=3,
        name="phase0_llm_calls_ranking",
        sql="""
        CREATE TABLE llm_calls (
            id                       TEXT PRIMARY KEY,
            purpose                  TEXT    NOT NULL,
            input_artifact_refs_json TEXT    NOT NULL,
            prompt_name              TEXT    NOT NULL,
            prompt_version           TEXT    NOT NULL,
            provider                 TEXT    NOT NULL,
            model_alias              TEXT    NOT NULL,
            model_id_reported        TEXT,
            parameters_json          TEXT    NOT NULL,
            response_json            TEXT,
            validation_status        TEXT    NOT NULL,
            attempts                 INTEGER NOT NULL,
            latency_ms               INTEGER NOT NULL,
            input_tokens             INTEGER,
            output_tokens            INTEGER,
            estimated_cost_usd       REAL,
            error                    TEXT,
            created_at               TEXT    NOT NULL
        );

        CREATE TABLE ranking_batches (
            id              TEXT PRIMARY KEY,
            transcript_id   TEXT NOT NULL REFERENCES transcripts(id),
            scoring_version TEXT NOT NULL,
            weights_version TEXT NOT NULL,
            prompt_version  TEXT NOT NULL,
            model_alias     TEXT NOT NULL,
            created_at      TEXT NOT NULL
        );
        CREATE INDEX ranking_batches_transcript_idx ON ranking_batches(transcript_id);

        CREATE TABLE ranking_runs (
            id               TEXT PRIMARY KEY,
            ranking_batch_id TEXT NOT NULL REFERENCES ranking_batches(id),
            candidate_id     TEXT NOT NULL REFERENCES candidates(id),
            feature_json     TEXT NOT NULL,
            risk_json        TEXT NOT NULL,
            final_score      REAL NOT NULL CHECK (final_score >= 0 AND final_score <= 100),
            rationale        TEXT NOT NULL,
            llm_call_id      TEXT REFERENCES llm_calls(id),
            created_at       TEXT NOT NULL,
            UNIQUE (ranking_batch_id, candidate_id)
        );
        """,
    ),
    Migration(
        version=4,
        name="phase0_editorial_claims_reviews",
        sql="""
        CREATE TABLE editorial_versions (
            id                     TEXT PRIMARY KEY,
            candidate_id           TEXT    NOT NULL REFERENCES candidates(id),
            version                INTEGER NOT NULL,
            hook                   TEXT    NOT NULL,
            commentary_before      TEXT    NOT NULL,
            commentary_after       TEXT    NOT NULL,
            excerpt_plan_json      TEXT    NOT NULL,
            title                  TEXT    NOT NULL,
            title_options_json     TEXT    NOT NULL,
            cta                    TEXT,
            transformation_summary TEXT    NOT NULL,
            status                 TEXT    NOT NULL,
            prompt_version         TEXT    NOT NULL,
            model_alias            TEXT    NOT NULL,
            llm_call_id            TEXT    REFERENCES llm_calls(id),
            created_at             TEXT    NOT NULL,
            UNIQUE (candidate_id, version)
        );

        CREATE TABLE claims (
            id                   TEXT PRIMARY KEY,
            editorial_version_id TEXT NOT NULL REFERENCES editorial_versions(id),
            claim_text           TEXT NOT NULL,
            claim_type           TEXT NOT NULL,
            importance           TEXT NOT NULL,
            evidence_refs_json   TEXT NOT NULL,
            confidence           REAL,
            status               TEXT NOT NULL,
            reviewer_note        TEXT,
            origin               TEXT NOT NULL,
            created_at           TEXT NOT NULL
        );
        CREATE INDEX claims_version_idx ON claims(editorial_version_id);

        CREATE TABLE review_events (
            id                TEXT PRIMARY KEY,
            object_type       TEXT NOT NULL,
            object_id         TEXT NOT NULL,
            decision          TEXT NOT NULL,
            reason_codes_json TEXT NOT NULL,
            notes             TEXT,
            reviewer          TEXT NOT NULL,
            created_at        TEXT NOT NULL
        );
        CREATE INDEX review_events_object_idx ON review_events(object_type, object_id, created_at);
        """,
    ),
)
