# Project Bible — Vertical Media Engine

## 1. Executive definition

Vertical Media Engine (VME) is a content-intelligence and transformation platform. It ingests long-form audio/video that is explicitly permitted by a source policy, converts it into timestamped semantic units, ranks candidate moments, adds original editorial context, renders vertical short-form drafts, collects human approval/rejection labels, publishes only after explicit approval, and maps downstream audience behavior back to the exact content and generation decisions that produced it.

The long-term business is not “automated clipping”. The long-term business is an evidence-driven distribution engine with four possible revenue layers:

1. owned media and audience;
2. B2B content repurposing for creators/companies;
3. performance distribution;
4. proprietary content-response data and ranking technology.

The first technical objective is therefore not scale. It is predictive validity.

## 2. Core hypothesis

Long-form content contains many plausible short-form moments, but only a small subset perform unusually well. Human editors use tacit signals such as authority, surprise, tension, novelty, clarity, emotional intensity and topical relevance. VME should formalize those signals, score candidate segments, test the predictions against real performance and improve the ranking process over time.

The moat, if one emerges, is the dataset:

`source content -> extracted features -> editorial treatment -> distribution context -> audience response`

The system must retain enough provenance to determine why a particular clip was selected and how it was transformed.

## 3. What the product is not

VME is not:
- a bot that downloads arbitrary popular videos and reposts them;
- a “fair use by duration” engine;
- a generic AI video editor;
- an autonomous financial-news publisher;
- a system that uses view count as its only success metric;
- an excuse to generate thousands of near-identical AI Shorts.

YouTube's monetization rules explicitly distinguish meaningful transformation from reused or mass-produced/inauthentic content. VME should be designed toward visible original contribution, not minimal edits.

## 4. Initial editorial verticals

The commercial experiment may start with English-language:
- markets / investing / macro;
- business / founders / strategy;
- AI / technology.

However, the software core must remain vertical-agnostic. Vertical-specific prompt/config packs can define terminology, risk rules and scoring weights.

Finance requires heightened factual controls. A source statement must be classified before transformation:
- verified factual statement;
- speaker opinion;
- forecast/estimate;
- company guidance;
- reported third-party claim;
- rhetorical/hypothetical statement.

The editorial system must preserve that distinction.

## 5. User roles

### Operator / Editor

The initial human operator:
- approves sources and source policies;
- reviews candidate clips;
- verifies high-risk factual claims;
- approves final drafts;
- explicitly triggers publishing;
- labels false positives and false negatives;
- reviews performance reports.

### Future B2B Client

May later:
- connect owned content;
- specify brand voice and clip rules;
- approve drafts;
- schedule distribution;
- review analytics.

Do not build multi-tenancy before the single-operator workflow works.

## 6. End-to-end product flow

### 6.1 Source registration

A source must be registered with:
- canonical identifier/URL/path;
- source type;
- owner/publisher;
- rights policy;
- permitted actions;
- evidence/reference for the rights basis;
- expiration/review date if relevant.

If there is no rights policy, ingestion stops.

### 6.2 Media ingestion

Phase 0 should support a local authorized file first.

Later adapters may support:
- creator-connected storage;
- podcast RSS where permitted;
- YouTube API metadata;
- creator-owned YouTube channels;
- licensed feeds;
- manually approved URL workflows.

Do not make browser scraping the primary ingestion architecture.

### 6.3 Transcription

Output should contain:
- text;
- word/segment timestamps;
- speaker labels where feasible;
- language;
- confidence metadata;
- provider/version.

The original transcript is immutable. Corrected/enriched transcript versions are separate derived artifacts.

### 6.4 Semantic segmentation

Create candidate semantic spans, not arbitrary fixed 30-second windows.

A span should have:
- start/end;
- lead-in context;
- speaker;
- topic;
- self-containedness;
- key claims;
- dependency on earlier context.

A candidate can later choose a tighter clip boundary.

### 6.5 Candidate ranking

The initial ranking engine is heuristic + LLM structured evaluation.

Candidate dimensions may include:
- hook strength;
- novelty;
- authority/credibility;
- emotional intensity;
- tension/controversy;
- information density;
- clarity/self-containedness;
- topical relevance;
- quotability;
- visual/editability potential;
- audience fit;
- risk penalty.

Do not collapse everything into one opaque score. Persist components.

Example conceptual form:

`V = Σ(w_i * normalized_feature_i) - risk_penalties`

Weights are configuration, not hard-coded truth.

### 6.6 Editorial transformation

A selected candidate should normally add original value through one or more of:
- contextual hook;
- explanation;
- critique/analysis;
- factual context;
- chart/data point;
- synthesis;
- visual framing;
- original narration/commentary.

The exact treatment depends on rights policy and vertical.

A clip that is only cropped, captioned and re-uploaded is not the target product.

### 6.7 Fact checking

Generated factual additions are claims. Claims require:
- normalized claim text;
- claim type;
- supporting source(s);
- evidence excerpt or structured evidence reference;
- confidence;
- reviewer status.

If evidence is missing, the claim must be removed, softened or flagged for human review.

### 6.8 Rendering

Phase 0 render:
- 9:16;
- 1080x1920 target;
- H.264/AAC or broadly compatible MP4;
- captions;
- safe text margins;
- deterministic template;
- optional hook card/text;
- no unlicensed music by default.

Use FFmpeg as the deterministic media core.

### 6.9 Human review

Every draft has a state:
- generated;
- needs_review;
- approved;
- rejected;
- blocked_rights;
- blocked_factcheck;
- published;
- retired.

Review labels should capture rejection reasons, not just boolean approval.

### 6.10 Publishing

Publishing is not part of Phase 0.

When enabled:
- OAuth/token scopes must be minimal;
- action must be explicit;
- title/description/attribution metadata must be reviewable;
- publish event must be audit logged;
- retry logic must not duplicate uploads;
- platform quotas must be observed.

### 6.11 Analytics

Each publication must link back to:
- source;
- candidate;
- editorial version;
- render version;
- prompt/model version;
- channel;
- publish time;
- experiment assignment.

Without this, learning is impossible.

## 7. MVP success criteria

The MVP is successful if it can process a known authorized source and produce three or more reviewable short-form drafts with complete provenance.

The ranking experiment is successful only if, on a labeled golden set, top-ranked candidates show measurable agreement with human selections and later, after publishing begins, measurable performance lift versus a baseline selection method.

The MVP is not judged by number of lines of code or number of agents.

## 8. Operator experience target

The eventual operator loop should feel like:

1. source appears;
2. rights state is visible;
3. top candidate clips are ranked;
4. operator previews;
5. each candidate shows “why selected” and risks;
6. operator approves/rejects quickly;
7. approved clip is rendered;
8. factual claims and provenance are visible;
9. publishing is explicit;
10. performance is fed back automatically.

The UI can wait. The workflow cannot.

## 9. Cost philosophy

Strong models should not process every possible second of media if cheaper deterministic or lightweight methods can narrow the set first.

Preferred funnel:
1. deterministic transcript/segmentation;
2. inexpensive feature/filter stage;
3. stronger model on shortlisted candidates;
4. strongest reasoning only for ambiguous/high-value editorial decisions.

Store usage and estimated cost per stage.

## 10. Experimentation philosophy

Every meaningful editorial change should be versioned:
- ranking weights;
- prompt;
- target duration;
- hook style;
- caption style;
- commentary ratio;
- publish time;
- target audience/channel.

Do not “learn” by anecdote. Use cohorts and baselines.

## 11. Long-term extensions

Only after core validation:
- creator accounts and brand profiles;
- B2B content ingestion;
- multi-platform distribution;
- automated experiment allocation;
- recommendation/ranking model trained on proprietary labels;
- channel portfolio optimizer;
- performance-based creator marketplace;
- structured content-response dataset products.

## 12. Kill criteria

The project should not be scaled blindly.

Re-evaluate if, after a meaningful labeled/published sample:
- ranking does not beat a simple baseline;
- human approval rate remains very low;
- rights/compliance workload overwhelms automation benefits;
- transformed content repeatedly fails platform authenticity/quality standards;
- cost per approved output is structurally uneconomic.

The desired outcome is a useful engine, not an expensive content factory.
