# Rights, Platform and Editorial Guardrails

This document is a product requirement, not optional legal decoration.

## 1. Default rule

No source may proceed to clip extraction/render/publishing unless the system can evaluate an explicit `RightsPolicy`.

Unknown status = block.

A public webpage or public video is not, by itself, proof of permission to download, transform or commercially republish the material.

## 2. Rights policy classes

### OWNED

The operator/client owns the relevant content and has authority to process/publish it.

### EXPLICIT_LICENSE

A written or otherwise recorded license permits the intended use. Store reference/evidence and any scope restrictions.

### CREATOR_AUTHORIZATION

The original creator has authorized clipping/republishing. Record scope, platforms, term, attribution and commercial conditions.

### PUBLIC_DOMAIN_VERIFIED

Use only after verification applicable to the work/jurisdiction. Do not infer public domain from age alone.

### TRANSFORMATIVE_REVIEW_REQUIRED

The source may be considered for a genuinely transformative editorial use, but each planned publication needs heightened human rights review.

This state is not a legal conclusion and must never be automatically upgraded to publishable.

### BLOCKED

No processing beyond metadata necessary for the block record.

### UNKNOWN

Fail closed.

## 3. No fake duration rules

Do not implement:
- “under 5 seconds is safe”;
- “under 10 seconds is fair use”;
- “under 30 seconds is legal”.

Copyright analysis is contextual. Duration can be relevant but is not an automatic safe harbor.

## 4. YouTube monetization design requirement

YouTube's current channel monetization guidance distinguishes meaningful original commentary/substantive modification/value from reused content. It also treats repetitive or mass-produced low-value content as inauthentic/non-monetizable.

Therefore VME should structurally favor:
- original commentary;
- analysis/critique;
- added context;
- substantial editing;
- unique narrative;
- transparent creation identity.

Cropping + subtitles + generic AI hook is not enough as a product strategy.

Also note: YouTube's reused-content monetization policy is separate from copyright enforcement. Permission from a creator does not automatically guarantee YPP monetization.

## 5. Source policy gate

Before ingestion:
```text
if can_ingest != true:
    BLOCK

if requested_action == "clip" and can_extract_clip != true:
    BLOCK

if requested_action == "render_transform" and can_transform != true:
    BLOCK

if requested_action == "publish" and can_publish != true:
    BLOCK
```

No downstream module can override this gate.

The flags are trustworthy only because the `RightsPolicy` model enforces D009: `BLOCKED`/`UNKNOWN` cannot carry a true flag, `TRANSFORMATIVE_REVIEW_REQUIRED` cannot carry `can_publish`, and an expired policy evaluates as `BLOCKED`. Tests must prove all three.

## 6. Factual integrity

Generated commentary creates new editorial responsibility.

Every added factual claim must be classified:
- fact;
- opinion attribution;
- forecast;
- estimate;
- guidance;
- allegation/reported claim;
- hypothetical.

Example:

Source:
“Personally, I think revenue could double.”

Forbidden transformation:
“Company expects revenue to double.”

Acceptable transformation:
“The speaker said they think revenue could double.”

For finance/business, material numeric claims should receive heightened verification.

## 7. Evidence standard

A claim may reach `SUPPORTED` only if:
- the evidence actually supports the same proposition;
- source identity/date are available;
- the claim has not been materially strengthened;
- context does not reverse meaning.

Search snippets alone should not be treated as final evidence where stronger primary material is available.

## 8. Publishing gate

Before any public publication:
- rights state permits publish;
- required attribution is present;
- no claim remains `UNVERIFIED` if presented as fact;
- human approval exists;
- render passed technical validation;
- metadata is reviewed;
- synthetic/AI-content disclosure is evaluated: if the draft contains generated narration, synthetic voice or generated visuals, the platform's current altered/synthetic-content disclosure rule is checked and the disclosure flag is stored in publication metadata (verify the current YouTube rule at implementation time; Phase 0 text-only commentary — D014 — keeps this out of scope until TTS/video generation is introduced);
- platform-specific restrictions are satisfied.

## 9. Platform API

Use official APIs where practical.

Do not design around evasion of platform rate limits, bot protections or access controls.

YouTube Data API quotas change. Query current quota configuration at implementation time rather than baking old assumptions into business logic.

As of 2026-09-09, Google documentation describes granular default quota buckets for `search.list` and `videos.insert` plus a separate general daily quota. Treat these as configuration/operational limits, not constants.

## 10. Mass automation risk

Do not enable autonomous high-volume publishing merely because the software can do it.

A production safety limit should exist:
- max publications/channel/day;
- max autonomous cost/day;
- max failed jobs;
- emergency kill switch;
- manual credential revocation path.

## 11. Auditability

Persist:
- who approved a source;
- rights basis;
- who approved a publication;
- claim evidence;
- generated text version;
- source timecodes;
- render hash;
- platform ID.

If a clip is challenged, VME should be able to reconstruct what happened.

## 12. External references (verify before production)

YouTube channel monetization policies:
https://support.google.com/youtube/answer/1311392

YouTube monetizable content:
https://support.google.com/youtube/answer/2490020

YouTube Data API getting started / quota:
https://developers.google.com/youtube/v3/getting-started

YouTube API quota/compliance audits:
https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits

## 13. Source supply

Authorized content is the scarce input. Keep `docs/SOURCES.md` current: one row per source with owner, rights basis, evidence reference, permitted actions, term and the date it was last reviewed. Phase 1 does not start below the threshold in D017. Do not widen source supply by relaxing the rights gate.
