# Implemented Pipeline (Phase 0)

Every other file under `docs/` describes design intent. This one describes what the code
actually does today, what it writes, and what it costs. Numbers come from one measured run
(`report cost` over the run's own database), not from estimates.

## 1. Stages

```text
source add ─┐
policy add ─┴─> RIGHTS GATE ─── block ──> stop, exit code 3
                    │
              media register      sha256 + ffprobe            → media_assets
                    │
              transcribe          faster-whisper, local       → transcripts (RAW, immutable)
                    │
              segment             deterministic, no LLM       → candidates
                    │
              rank                cheap → strong funnel       → ranking_batches, ranking_runs, llm_calls
                    │
              editorial generate  strong + claim extractor    → editorial_versions, claims, llm_calls
                    │
              ── HUMAN BOUNDARY ──  claim resolve / review approve|reject → review_events
                    │
              render plan         deterministic timeline      → render_plans (+ JSON artifact)
                    │
              render run          FFmpeg 1080x1920            → renders (+ MP4 artifact)
```

`pipeline run` chains the automated stages (register → editorial) under one correlation id
and stops at the first blocked or failed stage. Everything past the human boundary stays a
separate, explicit command.

Two gates cannot be bypassed:

- **Rights.** `ingest` before transcription and segmentation, `render_transform` before
  editorial generation, `clip` + `render_transform` before planning and rendering, and
  again at approval time. An expired or missing policy blocks regardless of flags.
- **Fact check.** A draft carrying any `UNVERIFIED` claim is `blocked_factcheck` and cannot
  be approved. Phase 0 resolution is human-only: `HUMAN_APPROVED` or `REMOVED` (D010).

## 2. What each stage writes

| Stage | Database | Filesystem |
|---|---|---|
| `media register` | `media_assets` (sha256, codecs, duration) | nothing; the file stays where it is (D018) |
| `transcribe` | `transcripts` (segments + word timestamps, provider version) | — |
| `segment` | `candidates` (span, text, context before/after) | — |
| `rank` | `ranking_batches`, `ranking_runs` (12 components, 5 penalties, tier, rationale), `llm_calls` | — |
| `editorial generate` | `editorial_versions`, `claims`, `llm_calls` | — |
| `claim resolve`, `review …` | `review_events`, status transitions | — |
| `render plan` | `render_plans` | `artifacts/render_plans/<id>.json` |
| `render run` | `renders` (sha256, validation verdict) | `artifacts/renders/<id>.mp4` |

Every LLM call that influences an output is a row: prompt name and version, model alias,
the model id the provider actually reported, parameters, the validated response, attempts,
latency and token usage. A ranking or a draft can be reconstructed from stored inputs.

The final artifact is a 1080x1920 H.264/AAC MP4: hook card, the source excerpt letterboxed
over a blurred copy of itself with burned-in captions, an outro card, and an attribution
overlay when the policy requires one. No narration, no synthetic voice, no generated
visuals (D014, D023).

## 3. Cost

Prices are not in the code. Token usage is recorded per call at run time and a versioned
price list is applied by `vme report cost` (D021, D025), so a price change re-prices
history. The report names unpriced models instead of costing them at zero, and flags the
total as a lower bound when provider attempts were billed but their usage never reached
the SDK.

Measured on one 16 min 38 s English source (26 candidates, 2 ranking batches, 6 drafts,
6 renders), Opus 5 as `strong` and Haiku 4.5 as `cheap`:

| Stage | Model | Calls | Cost |
|---|---|---:|---:|
| candidate scoring | Haiku 4.5 | 52 | $0.188 |
| candidate scoring | Opus 5 | 10 | $0.335 |
| claim extraction | Haiku 4.5 | 6 | $0.013 |
| editorial transform | Opus 5 | 6 | $0.519 |
| **total** | | **75** | **$1.056** |

Derived unit costs:

| Unit | Cost |
|---|---:|
| One candidate screened by the cheap tier | $0.0036 |
| One candidate scored by the strong tier | $0.033 |
| One editorial draft (transform + claim extraction) | $0.089 |
| One ranking batch over 26 candidates | $0.26 |
| One approved short (ranking amortised over 3 drafts) | $0.18 |

The funnel behaves as intended: cheap-tier spend grows with source length while strong-tier
spend is capped by `finalists_k`. A one-hour source implies roughly 100 candidates, about
$0.36 of screening plus a fixed $0.17 of finalist scoring; drafts stay $0.089 each.

Local stages cost no API money but do cost wall-clock: transcription with `small` on CPU
ran at roughly half real time (8 minutes for 16.5 minutes of audio), and one render takes
about 40 seconds at the `medium` preset.

## 4. Known accounting limits

- An attempt rejected by schema validation is billed by the provider, but the SDK raises
  before the usage block reaches us. Those attempts are counted in `unmetered_attempts`
  and make the total a lower bound. In the measured run: 3 attempts, likely a few cents.
- Cache reads and writes are not modelled; the pipeline does not use prompt caching yet.
- `estimated_cost_usd` on `LlmCall` stays `NULL` on purpose. Cost is a reporting concern.

## 5. Phase 1 harness (built, waiting for sources)

`label add` records the seven DATA_MODEL §3 dimensions per candidate, append-only, with
rejection reasons validated against a versioned taxonomy (`reasons_v1`); `review reject`
uses the same taxonomy. `golden export` writes labeled candidates with their aggregated
judgement (majority decision, ties reject, graded relevance from the expected bucket) and
the ranking run they were scored in. `bench run` persists precision@k, NDCG@k, pairwise
agreement and score calibration for one batch together with scoring, weights and prompt
versions; `bench compare` flags any drop beyond a tolerance. A committed fixture pins the
Viral Score formula and weights so a change fails the test suite instead of passing
unnoticed. The Phase 1 entry condition (3 authorized sources, 3 hours, D017) still holds
for the human golden set.

A provisional bootstrap exists (D028, D029): 99 candidates from three public-domain
macro/economics sources (66 min), labeled blind by the assistant session and flagged
`provisional`, with per-source benchmarks and the cost report under
`fixtures/golden/bootstrap_v1/`. Against those labels the funnel approves 17 of its 20
strong-tier finalists and orders 70-80% of human-ordered pairs the same way; the full run
cost $1.10 in 119 calls. Provisional labels yield to the first human label on each
candidate, so the operator can confirm or overturn them one by one.

## 6. Not built

Publishing, dashboards, queues, evidence retrieval, TTS, smart crop, multi-tenancy. Draft
text cannot be edited from the CLI: a rejected draft is regenerated, not patched. Claim
de-duplication is textual, so the transformer and the extractor can both file the same fact
in different words.
