# Bootstrap golden set v1 (provisional, D028/D029)

Blind provisional labels on 99 candidates from three public-domain macro/economics sources
(66 min): FDR's first Fireside Chat "On the Banking Crisis" (1933), Keynes, *The Economic
Consequences of the Peace* ch. 1-2, Bastiat, *Economic Sophisms* ch. 2. Transcribed with
faster-whisper `base`, segmented with `segmenter:v0.1.0`, ranked with the `cheap`
(Haiku 4.5) -> `strong` (Opus 5) funnel, weights `viral_v0.1.0`, prompt
`candidate_scorer 1.0.0`.

**These labels are not human labels.** They were written by the assistant session
`claude-fable-5.1-provisional` reading the candidate texts without seeing any score, and
they are flagged `provisional`: the moment an operator labels a candidate, the provisional
label stops counting (D028). They exist to smoke-test the harness and to give the operator
a starting point to confirm or overturn, one candidate at a time.

| File | Content |
|---|---|
| `labels_provisional.jsonl` | 99 labels keyed by `candidate_key` (`<media sha256>:<start_ms>-<end_ms>`), importable with `vme label import --file … --provisional` into any database that holds the same media |
| `golden_provisional.jsonl` | candidates with aggregated judgement and the ranking run they were scored in |
| `benchmarks.json` | four `bench run` results (one per ranking batch) with every version that produced them |
| `cost_report.json` | `vme report cost` for the run: 119 LLM calls, $1.10 |

Results against the provisional labels:

| Source | n | approved | P@3 | P@5 | NDCG@5 | pairwise |
|---|--:|--:|--:|--:|--:|--:|
| FDR, On the Banking Crisis (1933) | 20 | 12 | 0.67 | 0.80 | 0.74 | 0.73 |
| Keynes, Consequences ch. 1 | 11 | 6 | 1.00 | 1.00 | 0.74 | 0.80 |
| Keynes, Consequences ch. 2 | 33 | 17 | 1.00 | 1.00 | 0.79 | 0.79 |
| Bastiat, Economic Sophisms ch. 2 | 35 | 21 | 0.67 | 0.60 | 0.55 | 0.70 |

Reading: 17 of the 20 strong-tier finalists were approved blind; pairwise agreement
0.70-0.80 means the system orders roughly three of four human-ordered pairs the same way.
Candidates scoring under 40 were approved about a third of the time, those over 40 about
three quarters, so the score is informative but far from calibrated. Bastiat's chapter,
where most excerpts are mid-argument fragments, is where boundaries hurt most.

To reproduce: `uv run python scripts/fetch_speech_fixtures.py`, register the four files
(see `docs/SOURCES.md` S004-S006), run `pipeline`-style stages up to `rank`, then
`vme label import --file fixtures/golden/bootstrap_v1/labels_provisional.jsonl --provisional --reviewer claude-fable-5.1-provisional`
and `vme bench run --batch <id>` per batch. Segmentation is deterministic given the
transcript; a different whisper build can shift boundaries, in which case unmatched keys are
reported by the import instead of silently dropped.
