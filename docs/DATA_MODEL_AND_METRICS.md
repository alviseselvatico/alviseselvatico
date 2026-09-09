# Data Model and Metrics

## 1. Minimum entities

### Source

Fields:
- `id`
- `kind`
- `canonical_uri`
- `publisher`
- `title`
- `created_at`
- `rights_policy_id`
- `status`

### RightsPolicy

Fields:
- `id`
- `basis_type`
- `basis_reference`
- `can_ingest`
- `can_extract_clip`
- `can_transform`
- `can_publish`
- `requires_attribution`
- `attribution_text`
- `territory_notes`
- `expiry_at`
- `review_required`
- `notes`

Suggested `basis_type`:
- `OWNED`
- `EXPLICIT_LICENSE`
- `CREATOR_AUTHORIZATION`
- `PUBLIC_DOMAIN_VERIFIED`
- `TRANSFORMATIVE_REVIEW_REQUIRED`
- `BLOCKED`
- `UNKNOWN`

`UNKNOWN` fails closed.

Invariants (enforced in the domain model, see D009):
- `basis_type in {BLOCKED, UNKNOWN}` => `can_ingest = can_extract_clip = can_transform = can_publish = false`;
- `basis_type == TRANSFORMATIVE_REVIEW_REQUIRED` => `can_publish = false` and `review_required = true`;
- `expiry_at` in the past => policy evaluates as `BLOCKED` regardless of flags;
- a `RightsPolicy` that violates an invariant must fail validation, not be silently corrected.

### MediaAsset

- `id`
- `source_id`
- `sha256`
- `path_or_object_key`
- `duration_ms`
- `width`
- `height`
- `audio_codec`
- `video_codec`
- `ingested_at`

### Transcript

- `id`
- `media_asset_id`
- `kind` — `RAW` | `CORRECTED` | `ENRICHED`
- `derived_from_id` — null for `RAW`; parent transcript for derived versions
- `version`
- `provider`
- `provider_version`
- `model_alias`
- `language`
- `raw_text`
- `segments_json` — segments with word-level timestamps where the provider supplies them
- `created_at`

`RAW` transcripts are immutable. Corrections create a new `CORRECTED` row pointing to the parent.

### Candidate

- `id`
- `transcript_id`
- `start_ms`
- `end_ms`
- `context_before`
- `context_after`
- `speaker`
- `topic`
- `candidate_text`
- `created_by`
- `created_at`

### RankingBatch

One row per ranking execution over a transcript. Lets two weight/prompt versions be compared on the same candidate set.

- `id`
- `transcript_id`
- `scoring_version`
- `weights_version`
- `prompt_version`
- `model_alias`
- `created_at`

### RankingRun

- `id`
- `ranking_batch_id`
- `candidate_id`
- `feature_json`
- `risk_json`
- `final_score`
- `rationale`
- `llm_call_id` — null when fully deterministic
- `created_at`

### LlmCall

Required by ARCHITECTURE §6. One row per LLM invocation that influences an output.

- `id`
- `purpose`
- `input_artifact_refs_json`
- `prompt_name`
- `prompt_version`
- `provider`
- `model_alias`
- `model_id_reported` — as returned by the provider, never configured in code
- `parameters_json`
- `response_json`
- `validation_status` — `VALID` | `INVALID_RETRIED` | `FAILED`
- `attempts`
- `latency_ms`
- `input_tokens`
- `output_tokens`
- `estimated_cost_usd`
- `created_at`

### EditorialVersion

- `id`
- `candidate_id`
- `version`
- `hook`
- `commentary`
- `title`
- `cta`
- `claims_json`
- `status` — see draft state machine below
- `prompt_version`
- `model_alias`
- `llm_call_id`
- `created_at`

Draft state machine (owned by `EditorialVersion.status`; `ReviewEvent` is the audit log of transitions, not the state):

```text
generated -> needs_review
needs_review -> approved | rejected
needs_review -> blocked_rights      (rights policy no longer permits the action)
needs_review -> blocked_factcheck   (any claim UNVERIFIED / CONTRADICTED / AMBIGUOUS)
blocked_factcheck -> needs_review   (all claims HUMAN_APPROVED, SUPPORTED or REMOVED)
approved -> published (Phase 3+)
approved | published -> retired
```

Only `approved` may reach render output export; only `approved` with a passing publishing gate may reach `published`.

### Claim

- `id`
- `editorial_version_id`
- `claim_text`
- `claim_type`
- `evidence_refs`
- `confidence`
- `status`
- `reviewer_note`

Statuses:
- `UNVERIFIED`
- `SUPPORTED`
- `CONTRADICTED`
- `AMBIGUOUS`
- `HUMAN_APPROVED`
- `REMOVED`

### RenderPlan

- `id`
- `editorial_version_id`
- `template_version`
- `timeline_json`
- `caption_config_json`
- `overlay_config_json`
- `created_at`

### Render

- `id`
- `render_plan_id`
- `file_path`
- `sha256`
- `duration_ms`
- `validation_json`
- `created_at`

### ReviewEvent

- `id`
- `object_type`
- `object_id`
- `decision`
- `reason_codes`
- `notes`
- `reviewer`
- `created_at`

### Publication

Later:
- `id`
- `render_id`
- `platform`
- `channel_id`
- `platform_content_id`
- `published_at`
- `metadata_json`
- `experiment_id`

### MetricSnapshot

Later:
- `publication_id`
- `captured_at`
- `views`
- `likes`
- `comments`
- `shares`
- `followers_gained`
- `watch_time`
- `average_view_duration`
- `completion_rate`
- platform-specific fields

Never pretend cross-platform metrics are perfectly equivalent.

## 2. Viral Score v0

The v0 score is a ranking heuristic, not an ML prediction.

Example components on a normalized 0-1 scale:
- `hook_strength`
- `novelty`
- `authority`
- `emotion`
- `tension`
- `information_density`
- `clarity`
- `self_containedness`
- `topic_relevance`
- `quotability`
- `visual_editability`
- `audience_fit`

Penalties (also 0-1):
- `context_dependency` — LLM
- `factual_risk` — LLM
- `brand_safety_risk` — LLM
- `overclaim_risk` — LLM
- `rights_risk` — deterministic, derived from the source `RightsPolicy` (never asked to the LLM)

Scale contract:
```text
components, penalties ∈ [0, 1]
component weights sum to 100
penalty weights are independent, each in [0, 100]
raw     = Σ component_weight_i * component_i          # ∈ [0, 100]
penalty = Σ penalty_weight_j * penalty_j
final   = clamp(raw - penalty, 0, 100)
```

Weights live in a versioned config file (`weights_version`), never in code. Persist the feature vector, the penalty vector, `weights_version` and the final score.

## 3. Human labels

Every candidate reviewed should capture:
- approve/reject;
- whether the selected boundary is correct;
- quality of hook;
- factual risk;
- rights risk;
- reason for rejection;
- expected performance bucket;
- optional edited version.

This creates training/evaluation data.

## 4. Golden set

Build a golden set before sophisticated ranking.

Target initial set:
- at least 50 candidates for basic regression testing;
- preferably 100+ before interpreting ranking quality seriously;
- include obvious good, obvious bad and ambiguous examples.

Do not train and evaluate on exactly the same manually tuned set without a holdout.

## 5. Ranking evaluation

Offline:
- top-k precision against human approved set;
- NDCG/rank correlation where useful;
- pairwise agreement;
- calibration by score bucket;
- approval rate by score decile.

Online later:
- performance lift of top-ranked selections vs baseline/random/chronological selection;
- watch/retention metrics;
- share rate;
- follow conversion;
- downstream newsletter/site conversion when available.

## 6. North-star metric

The north-star is not views.

Primary:
`performance lift of VME-ranked content versus a defined baseline, adjusted for channel/context where possible`.

Operational:
- `cost_per_approved_short`
- `minutes_operator_time_per_approved_short`
- `approval_rate`
- `pipeline_failure_rate`
- `factcheck_block_rate`
- `rights_block_rate`

## 7. Experiment identity

Every published output should know:
- ranking algorithm/version;
- prompt versions;
- template version;
- duration bucket;
- hook family;
- commentary style;
- posting time;
- channel/audience;
- source/speaker/topic.

Without version identity, later “learning” will be storytelling rather than analysis.
