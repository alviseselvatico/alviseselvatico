# Prompt Contracts

Prompts are product code. Version them.

The following are conceptual contracts. Claude Code should implement them as structured schemas and prompt templates only when their phase is reached.

## 1. Candidate Scorer

### Inputs
- candidate transcript text;
- context before/after;
- speaker metadata if known;
- vertical;
- target audience;
- current topic context if available;
- risk metadata.

### Output schema
```json
{
  "hook_strength": 0.0,
  "novelty": 0.0,
  "authority": 0.0,
  "emotion": 0.0,
  "tension": 0.0,
  "information_density": 0.0,
  "clarity": 0.0,
  "self_containedness": 0.0,
  "topic_relevance": 0.0,
  "quotability": 0.0,
  "visual_editability": 0.0,
  "audience_fit": 0.0,
  "context_dependency": 0.0,
  "factual_risk": 0.0,
  "brand_safety_risk": 0.0,
  "overclaim_risk": 0.0,
  "recommended_start_ms": 0,
  "recommended_end_ms": 0,
  "why_it_might_work": "...",
  "why_it_might_fail": "...",
  "key_claims": []
}
```

The LLM must not output the final weighted score if deterministic application code can calculate it. `rights_risk` is not an LLM output: it is derived from the rights policy by application code.

## 2. Editorial Transformer

Goal: add meaningful original editorial value without changing the meaning of the source.

Inputs:
- selected source excerpt;
- source context;
- claim classifications;
- permitted transformation policy;
- audience;
- target duration.

Outputs:
```json
{
  "hook": "...",
  "commentary_before": "...",
  "source_excerpt_plan": [],
  "commentary_after": "...",
  "title_options": [],
  "cta": null,
  "generated_claims": [],
  "transformation_summary": "What original value was added"
}
```

Rules:
- no invented facts;
- preserve opinion/forecast/guidance distinctions;
- no sensational certainty added to ambiguous source;
- no generic filler;
- no fabricated quotation.

## 3. Claim Extractor

Extract only claims introduced or materially restated by VME.

Output:
```json
{
  "claims": [
    {
      "text": "...",
      "type": "FACT|OPINION_ATTRIBUTION|FORECAST|ESTIMATE|GUIDANCE|ALLEGATION|HYPOTHETICAL",
      "importance": "LOW|MEDIUM|HIGH",
      "verification_required": true
    }
  ]
}
```

## 4. Fact Check Evaluator

Inputs:
- claim;
- evidence set;
- original source context.

Output:
```json
{
  "status": "SUPPORTED|CONTRADICTED|AMBIGUOUS|INSUFFICIENT",
  "reason": "...",
  "supporting_evidence_ids": [],
  "qualification_needed": null
}
```

It must be allowed to say insufficient.

## 5. Candidate Boundary Editor

Task:
Find the shortest coherent excerpt that preserves the intended meaning.

Output:
- `start_ms`
- `end_ms`
- `lead_in_needed`
- `context_loss_risk`
- `boundary_rationale`

Never cut a qualification that materially reverses or weakens a claim.

## 6. Performance Analyst (later)

Inputs:
- publication metrics;
- cohort/baseline;
- content features;
- channel;
- publish timing;
- experiment assignments.

Outputs:
- descriptive result;
- uncertainty;
- possible confounders;
- next test.

Rule:
Correlation is not causal proof. Do not auto-rewrite ranking weights from one viral result.

## 7. Prompt versioning

Each prompt should have:
- stable name;
- semantic version;
- changelog;
- output schema version;
- test examples.

A prompt change that alters output meaning should produce a new version.

## 8. Model routing

Do not hard-code one model for every task.

Routing aliases (D012): `cheap` and `strong`, resolved from configuration.
- `cheap`: coarse labeling/filtering;
- `strong`: candidate ranking finalists and editorial reasoning;
- deterministic code: score aggregation, time math, validation, FFmpeg plans;
- specialist transcription model: speech-to-text.

The application, not the prompt, controls the routing.
