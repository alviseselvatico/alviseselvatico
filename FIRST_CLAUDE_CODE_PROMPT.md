# First Claude Code Prompt

Paste the following into Claude Code from the repository root.

---

Read `CLAUDE.md`, `PROJECT_MANIFEST.yaml`, `docs/DECISIONS.md`, `docs/PROJECT_BIBLE.md`, `docs/ARCHITECTURE.md`, `docs/RIGHTS_AND_PLATFORM_GUARDRAILS.md`, `docs/DATA_MODEL_AND_METRICS.md` and `docs/ROADMAP.md`.

We are starting Phase 0 only. Decisions D001-D017 are final for this phase: do not re-open them, do not propose alternatives, do not add a decision unless a genuinely new material choice appears.

Your job is to build the first minimal vertical slice of Vertical Media Engine. Do not build the final platform, dashboard, cloud infrastructure, multi-tenancy or publishing.

First, inspect the repository and produce a short implementation plan for Phase 0 with:
- the minimal file/package structure (`src/vme/...`, created only where code needs it);
- dependency choices with one-line reasons (uv, ruff, mypy strict, pytest, pydantic, faster-whisper, an Anthropic SDK — nothing else unless justified);
- the first 5-10 implementation tasks in dependency order;
- tests/acceptance checks for each;
- any assumptions you still need to make (there should be very few).

Then implement milestone 1:

1. initialize the project with `uv` and a single `pyproject.toml` (ruff + mypy strict configured);
2. create typed Pydantic domain models for `Source`, `RightsPolicy` and `MediaAsset` following `docs/DATA_MODEL_AND_METRICS.md`;
3. enforce the D009 invariants inside `RightsPolicy` (validation error, never silent correction);
4. implement a fail-closed rights gate that checks the capability flags for a requested action;
5. implement local media registration: SHA-256 fingerprint plus ffprobe metadata, invoked via `subprocess` with an argument list, never a shell string;
6. create a CLI (`vme`) with `source add`, `policy add`, `media register` — registration of a media file must refuse when the source policy does not permit `ingest`;
7. use SQLite persistence with a small repository layer and explicit schema migrations;
8. add pytest coverage, including proof that `UNKNOWN`, `BLOCKED`, an expired policy, and a `TRANSFORMATIVE_REVIEW_REQUIRED` policy with `can_publish=true` all fail; generate any audio/video fixture at test time with `ffmpeg -f lavfi` (D016);
9. add structured JSON logging with a correlation id per CLI invocation; never log secrets or full media paths outside the artifacts directory;
10. keep `.env.example` as the only configuration reference; read configuration through one typed settings object;
11. append to `docs/DECISIONS.md` only if you make a material architectural choice not already recorded.

Constraints:
- Python 3.12+;
- do not add FastAPI, Redis, Celery, Docker, Postgres, a frontend, authentication, publishing, browser automation, TTS, evidence retrieval or smart crop;
- do not implement arbitrary web downloading;
- do not hard-code model IDs or provider secrets;
- do not create empty abstraction layers for future features;
- deterministic code wherever possible.

After implementation:
- run `uv run ruff check .`, `uv run mypy src`, `uv run pytest`;
- run the CLI end-to-end against a generated fixture: add an `OWNED` source, register the file, show the stored record; then show that the same file is refused under an `UNKNOWN` policy;
- show the resulting repo tree;
- summarize what works;
- list exact next tasks for milestone 2 (transcription adapter with faster-whisper, `Transcript` model with `RAW` kind, semantic segmentation into `Candidate` rows) but do not implement them until milestone 1 passes.

If a library/API detail is uncertain, use current documentation rather than guessing.
