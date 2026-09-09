# Roadmap

## Phase 0 — Vertical Slice

### Goal
Prove the pipeline locally on one explicitly authorized media file.

### Build
- project package and CLI;
- config;
- SQLite persistence;
- source + rights policy;
- media fingerprint/probe;
- transcription adapter;
- semantic segmentation;
- candidate scoring;
- top-k selection;
- editorial transformation;
- simple claim extraction (claims born `UNVERIFIED`, human-only resolution — D010);
- deterministic render plan (blurred-background letterbox, text-only commentary — D013, D014);
- FFmpeg vertical render;
- manual review command;
- structured logs;
- tests.

### Explicitly do not build
- evidence retrieval / web fact-checking (Phase 1);
- TTS or synthetic voice;
- speaker tracking / smart crop;
- public upload;
- dashboard;
- background queue;
- B2B accounts;
- cloud deployment;
- social network automation.

### Exit criteria
- one source completes end-to-end;
- >=5 candidates;
- >=3 valid rendered drafts;
- every output traceable to source timecodes and generation version;
- rights block tested (including D009 invariants);
- fact-risk block tested (an `UNVERIFIED` claim keeps the draft in `blocked_factcheck`).

## Phase 1 — Evaluation Harness

### Entry condition
`docs/SOURCES.md` lists >= 3 authorized sources totalling >= 3 hours of English speech, each with a stored rights policy and evidence (D017). Without this the golden set cannot be built.

### Goal
Determine whether ranking is useful.

### Build
- golden candidate dataset;
- labeling CLI/workflow;
- ranking regression tests;
- prompt version tracking;
- feature/weight configuration;
- evidence-retrieval adapter and automated claim evaluation (SUPPORTED / CONTRADICTED / AMBIGUOUS / INSUFFICIENT);
- optional hosted STT adapter with diarization if speaker attribution proves necessary;
- pairwise ranking evaluation;
- approval reason taxonomy;
- cost and latency reporting.

### Exit criteria
- >=50 labeled candidates;
- reproducible ranking benchmark;
- measurable top-k precision/agreement;
- no regression unnoticed when prompt/weights change.

## Phase 2 — Operator Workflow

### Goal
Make one person capable of reviewing large candidate volume quickly.

### Build
- simple review UI or local web app;
- video preview;
- edit hook/commentary;
- approve/reject;
- evidence view;
- render queue;
- PostgreSQL if justified;
- object storage if justified;
- Sentry staging;
- optional PostHog product analytics.

### Exit criteria
- operator can process a batch without terminal editing;
- full audit trail;
- no automatic publication.

## Phase 3 — Controlled Publishing

### Goal
Collect real performance data safely.

### Build
- official YouTube API adapter;
- OAuth/secrets handling;
- idempotent uploads;
- explicit publish button;
- quota monitor;
- publication ledger;
- metrics collector;
- experiment assignments;
- kill switch.

### Exit criteria
- every publication has explicit approval;
- no duplicate upload on retry;
- metrics map exactly to content/version;
- at least one controlled content experiment runs.

## Phase 4 — Predictive Learning

### Goal
Move from heuristics toward empirical ranking.

### Build
- sufficient labeled/performance dataset;
- baseline models;
- leakage-resistant train/test process;
- channel/topic normalization;
- calibration;
- experiment analysis;
- model registry.

Do not begin with a complex deep model. Start with interpretable baselines.

### Exit criteria
- ranking beats heuristic baseline out-of-sample;
- lift is repeatable;
- model decisions remain inspectable enough for editorial use.

## Phase 5 — Scale / Multi-channel

### Goal
Scale only proven formats.

Potential:
- worker queue;
- parallel rendering;
- channel configs;
- additional platforms;
- Postiz or native platform adapters;
- autoscheduling with human guardrails;
- per-channel budgets.

## Phase 6 — B2B Creator Product

Only after internal engine works:
- client-owned source connection;
- brand profiles;
- client approval portal;
- quotas;
- multi-tenancy;
- billing;
- permissions;
- creator performance dashboard.

## Phase 7 — Distribution Network / Marketplace

Potential long-term:
- creator-authorized clipping campaigns;
- performance-based payouts;
- tracked distribution;
- clipper/affiliate network;
- attribution and fraud controls.

This phase is strategically interesting but operationally and legally much more complex. It is not an MVP feature.
