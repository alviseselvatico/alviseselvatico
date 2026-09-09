# CLAUDE.md — Vertical Media Engine

You are working on VME, an AI-assisted vertical media engine. Read `PROJECT_MANIFEST.yaml` first. For architecture or product decisions, read the relevant file under `docs/` before coding.

## Mission

Build the smallest reliable system that can ingest an explicitly permitted source, transcribe it, identify high-potential segments, transform them with original editorial value, render vertical drafts, collect human review labels, and later learn from publishing performance.

The project is not a mass reposting bot. It is a content intelligence and transformation system.

## Development behavior

1. Work phase-by-phase. Do not jump to the final architecture.
2. Before implementing a major feature, inspect existing code and the relevant docs.
3. Prefer simple local implementations before services, queues or microservices.
4. Add tests with each meaningful feature.
5. Run tests, linters and relevant smoke checks after changes.
6. Do not silently change architecture. Record material decisions in `docs/DECISIONS.md`.
7. Do not hard-code model IDs, API keys, channel IDs or credentials.
8. Keep external providers behind small interfaces/adapters.
9. Store prompt version, model/provider, parameters and output for every LLM decision that matters.
10. Use structured outputs validated with typed schemas for LLM responses.
11. Never treat an LLM score as ground truth. Persist component scores and rationale.
12. Optimize token/API spend using funneling: deterministic preprocessing -> cheap filtering -> strong-model evaluation of finalists.
13. Avoid speculative abstractions. A module should exist because the current phase needs it.

## Mandatory safety/product guardrails

Read `docs/RIGHTS_AND_PLATFORM_GUARDRAILS.md` before implementing ingestion, downloading, rendering, publishing or source discovery.

Hard requirements:
- No source moves past ingestion without a stored rights policy.
- Unknown rights status defaults to BLOCK.
- Public URL != permission to download or republish.
- Do not create a fake safe-duration rule for copyright.
- No automatic public publishing before the roadmap explicitly enables it.
- Factual statements introduced by VME need evidence and provenance.
- Distinguish source statements, opinions, estimates, guidance and verified facts.
- Never expose secrets in logs, prompts, commits, screenshots or test fixtures.

## Toolchain (D015)

- `uv` for environment, lockfile and scripts; single `pyproject.toml`
- `ruff` for lint and format; `mypy --strict` on `src/`
- `pytest`; run `uv run pytest`, `uv run ruff check .`, `uv run mypy src` before declaring a task done
- Python 3.12+

## Phase 0 decisions already taken (do not re-decide; see docs/DECISIONS.md)

- Rights policy invariants are enforced in the model (D009)
- Fact check is human-only; generated claims start `UNVERIFIED` and block the draft (D010)
- Transcription: local `faster-whisper`, word timestamps, no diarization (D011)
- LLM: two aliases `cheap`/`strong` from environment; first provider Anthropic; no model IDs in code (D012)
- Reframe: blurred-background letterbox at 1080x1920 (D013)
- Commentary: text-only (hook card, captions, overlays); no TTS (D014)
- Fixtures: generated with `ffmpeg -f lavfi` at test time, plus one committed owner-recorded speech WAV under `fixtures/speech/` (D016)

## Phase 0 implementation preference

Start with:
- Python package
- CLI
- SQLite
- local filesystem
- FFmpeg/ffprobe
- one transcription adapter
- one LLM adapter
- Pydantic models
- pytest
- structured JSON logs

Do NOT start with:
- Kubernetes
- microservices
- Kafka
- vector database
- elaborate frontend
- multi-tenant auth
- payments
- auto-publish
- custom model training
- evidence retrieval / web fact-checking
- TTS / synthetic voice
- speaker tracking / smart crop

## Suggested repository shape

```text
src/vme/
  domain/
  ingestion/
  transcription/
  segmentation/
  ranking/
  editorial/
  rendering/
  rights/
  factcheck/
  storage/
  cli/
tests/
fixtures/
artifacts/
docs/
```

This is a suggestion, not an excuse to scaffold empty architecture. Create directories only when code needs them.

## Definition of done for a task

A task is not done because code was written. It is done when:
- behavior matches the current phase requirements;
- tests pass;
- errors are handled visibly;
- logging/provenance is sufficient to reproduce the result;
- no secret or rights bypass was introduced;
- documentation is updated if behavior or architecture changed.

## Git discipline

Keep commits small and coherent. Do not rewrite history unless explicitly asked. Never force push by default. Never commit `.env`, credentials, raw private media, downloaded third-party content or large generated artifacts.

## When uncertain

Do not invent external API behavior. Use current documentation (Context7/web/docs) and cite the relevant version in code comments only when it prevents future ambiguity.

If a product/legal rule is ambiguous, fail closed and surface it to the operator.
