# Speech fixtures (D016, D019)

Transcription tests need real English speech. Two kinds of fixture exist; tests use the
first one they find and otherwise **skip with an explicit reason** (never a fake transcript).

## 1. Operator-owned clip (committed) — `sample_en.wav`

Place here ONE operator-recorded clip: `sample_en.wav`, <= 90 s, mono, 16 kHz, English.
It is the only media committed to the repository (`.gitignore` allow rule) and is registered
as source S001 with an `OWNED` policy (`can_publish=false`).

## 2. Third-party public-domain samples (downloaded, never committed) — `external/`

```bash
uv run python scripts/fetch_speech_fixtures.py
```

The script downloads pinned files into `fixtures/speech/external/` (git-ignored), verifies
the SHA-256 of the exact bytes and prints the rights evidence for each. Current manifest:

| name | file | length | rights basis | evidence |
|------|------|--------|--------------|----------|
| `jfk` | `external/jfk.wav` | 11 s | public domain, US federal government work (17 U.S.C. §105) — candidate, operator to confirm | JFK inaugural address 1961-01-20; copy from `ggerganov/whisper.cpp` `samples/jfk.wav` |

Candidates reviewed but **not** added:

- `George W. Bush's weekly radio address (November 1, 2008).oga` on Wikimedia Commons
  (~3.5 MB, several minutes of clear English). Commons metadata: `License: Public domain`,
  `Copyrighted: False`, credit `georgewbush-whitehouse.archives.gov`. Not pinned yet because
  the Commons upload host rate-limited this environment; add it to the manifest with its
  SHA-256 once downloaded by the operator.
- `mozilla/DeepSpeech` `data/smoke_test/LDC93S1.wav`: LDC/TIMIT sample, **not** free — rejected.
- `Uberi/speech_recognition` `examples/english.wav`, `alphacep/vosk-api` `python/example/test.wav`:
  audio provenance/license not documented — rejected.
- Mozilla Common Voice (CC0) and LibriSpeech/LibriVox (CC BY 4.0 / public domain): valid
  Phase 1 supply options but not fetchable as a single pinned GitHub file.

These fixtures serve tests only. Using a public-domain recording as a real VME source still
requires a `RightsPolicy` row with evidence (`PUBLIC_DOMAIN_VERIFIED` is an operator decision,
never inferred by code — guardrails §2).
