# Authorized Sources

Operator-maintained register. One row per source. Phase 1 entry condition: >= 3 sources, >= 3 hours of English speech in total, every row with evidence (D017).

| id | title / channel | owner | rights basis | evidence ref | ingest | clip | transform | publish | term / expiry | last reviewed | notes |
|----|-----------------|-------|--------------|--------------|--------|------|-----------|---------|---------------|---------------|-------|
| S001 | (Phase 0 fixture) operator-recorded speech clip | operator | OWNED | fixtures/speech/README.md | yes | yes | yes | no | — | 2026-09-09 | test only, never publish |
| S002 | (test fixture) JFK inaugural address excerpt, 11 s, `fixtures/speech/external/jfk.wav` | US federal government (public domain) | PUBLIC_DOMAIN_VERIFIED — operator confirmation pending | fixtures/speech/README.md; 17 U.S.C. §105; ggerganov/whisper.cpp samples/jfk.wav | yes | yes | yes | no | — | pending | tests only, never publish; not counted toward D017 |
| S003 | (test fixture) LibriVox, The Heart of a Mystery ch. 1, 16 min, `fixtures/speech/external/librivox_heartofamystery_01.mp3` | LibriVox volunteers (public domain release) | PUBLIC_DOMAIN_VERIFIED — operator confirmation pending | fixtures/speech/README.md; archive.org heartofamystery_2005_librivox (publicdomain/mark/1.0) | yes | yes | yes | no | — | pending | tests + pipeline validation only, never publish; audiobook, not a talk; not counted toward D017 |

Rules:
- a row without an evidence reference is `UNKNOWN`, i.e. blocked;
- `publish` stays `no` until Phase 3 and an explicit review;
- do not add a source here to work around the gate: the register documents rights, it does not grant them.
