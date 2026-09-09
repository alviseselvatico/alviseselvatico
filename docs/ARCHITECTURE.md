# Architecture

## 1. Architectural principle

Start as a modular monolith. Split services only when measured scale, isolation or failure domains justify it.

Phase 0 should be a local Python application with clear module boundaries and typed data contracts.

## 2. Logical pipeline

```text
Source Registry
      |
      v
Rights Gate ---- BLOCK
      |
      v
Media Ingestion
      |
      v
Transcription
      |
      v
Semantic Segmentation
      |
      v
Candidate Feature Extraction
      |
      v
Ranking / Viral Score
      |
      v
Editorial Transformation
      |
      +----> Claim Extraction -> Fact Check Gate ---- BLOCK/REVIEW
      |
      v
Render Plan
      |
      v
FFmpeg Renderer
      |
      v
Human Review
      |
      v
Publish Queue (later)
      |
      v
Platform APIs
      |
      v
Metrics Collector
      |
      v
Experiment + Learning Store
```

## 3. Domain boundaries

### `rights`

Owns:
- source policy;
- permitted actions;
- blocked states;
- policy checks.

No downstream module can bypass `rights`.

### `ingestion`

Owns:
- local file validation;
- media metadata;
- hashing;
- source artifact registration.

It should not decide rights.

### `transcription`

Owns:
- provider adapter;
- transcript generation;
- timestamps;
- transcript artifact versioning.

### `segmentation`

Owns:
- semantic spans;
- candidate boundaries;
- context windows.

### `ranking`

Owns:
- feature schema;
- scoring configuration;
- ranking;
- rationale;
- risk penalties.

### `editorial`

Owns:
- hook;
- commentary;
- structure;
- title variants;
- render-plan text.

It must not assert unverified facts.

### `factcheck`

Owns:
- generated claim extraction;
- evidence linkage;
- claim state;
- gate decision.

### `rendering`

Owns:
- subtitles;
- crop/reframe;
- overlays;
- FFmpeg commands;
- media validation.

Rendering should be deterministic from a stored `RenderPlan`.

### `storage`

Owns:
- repositories/persistence;
- migrations;
- artifact paths.

### `publishing` (later)

Owns:
- OAuth/token adapter;
- idempotent publish operation;
- metadata;
- platform response;
- retries.

### `analytics` (later)

Owns:
- metrics snapshots;
- normalization;
- cohort mapping;
- experiments.

## 4. Core artifact immutability

The system should treat these as versioned/immutable records:
- source media fingerprint;
- raw transcript;
- transcript version;
- candidate definition;
- ranking run;
- editorial version;
- render plan;
- rendered asset;
- publication;
- metric snapshot.

Avoid overwriting history.

## 5. External provider interfaces

Create small provider protocols/interfaces for:
- speech-to-text;
- LLM structured generation;
- evidence search/retrieval;
- object storage;
- publishing platforms.

Do not make core domain logic dependent on a provider SDK.

## 6. LLM call contract

Every LLM call that influences an output must record:
- purpose;
- input artifact IDs;
- prompt name/version;
- provider;
- model alias/ID;
- parameters;
- structured response;
- validation outcome;
- latency;
- usage/cost if available;
- timestamp.

Use typed JSON outputs. Retry invalid structure with bounded attempts.

## 7. Error handling

Every pipeline job should end in a visible state:
- success;
- retryable_failure;
- permanent_failure;
- blocked_policy;
- needs_human_review.

Never swallow failures.

## 8. Local Phase 0 persistence

SQLite is acceptable for the first vertical slice.

Media artifacts:
```text
artifacts/
  source/
  transcript/
  candidates/
  render_plans/
  renders/
  logs/
```

Do not commit `artifacts/`.

## 9. Upgrade path

### When to move to PostgreSQL

Move once:
- concurrent jobs appear;
- analytics queries become material;
- operator UI needs shared state;
- multiple machines/processes are used.

### When to add a queue

Add Redis + a worker queue when:
- transcription/render tasks block operator flow;
- concurrent processing is needed;
- retries need durable orchestration.

### When to add object storage

Add S3-compatible storage when:
- deployment leaves one machine;
- media must survive ephemeral workers;
- B2B uploads begin.

### When to split services

Only after a concrete reason such as:
- separate scaling;
- security boundary;
- operational ownership;
- incompatible runtime.

## 10. Suggested later stack

A plausible target, not a Phase 0 requirement:
- Python/FastAPI application;
- PostgreSQL;
- Redis + worker(s);
- S3/R2-compatible object storage;
- Next.js operator dashboard;
- FFmpeg workers;
- provider adapters;
- PostHog;
- Sentry;
- Docker;
- managed hosting appropriate to workload.

## 11. Security

Minimum:
- secrets only via environment/secret manager;
- least-privilege API tokens;
- `.env` ignored;
- no raw token logging;
- media/artifact paths sanitized;
- input files probed before processing;
- FFmpeg commands built safely, not shell-concatenated from untrusted text;
- outbound network access conceptually limited by adapter;
- publishing credentials separated from analysis workers.

## 12. Performance

Do not optimize before profiling.

Likely expensive stages:
- transcription;
- strong-model ranking/editorial calls;
- rendering;
- source media transfer.

Cache stable derived artifacts by source fingerprint + configuration version.
