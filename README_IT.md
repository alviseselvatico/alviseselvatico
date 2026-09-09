# Vertical Media Engine — Starter Repository per Claude Code

Questo pacchetto è la “bibbia operativa” del progetto, strutturata per essere letta e usata direttamente da Claude Code.

## Obiettivo

Costruire un motore che trasformi contenuti long-form autorizzati o utilizzabili secondo una policy definita in short-form originale e trasformativo, con:

- ingestion e trascrizione;
- segmentazione e ranking dei momenti migliori;
- generazione di hook, commentary e contesto originale;
- rendering 9:16 con sottotitoli;
- human review;
- publishing controllato;
- raccolta analytics;
- apprendimento dai risultati;
- futura estensione B2B per creator e media network.

La priorità non è “pubblicare più clip possibile”. È costruire un sistema che impari a prevedere quali segmenti e quali strutture editoriali generano performance, mantenendo diritti, accuratezza e qualità sotto controllo.

## Come iniziare

1. Decomprimi la cartella.
2. Apri un terminale nella root del progetto.
3. Inizializza Git:
   ```bash
   git init
   git add .
   git commit -m "chore: initialize project bible"
   ```
4. Avvia Claude Code nella root:
   ```bash
   claude
   ```
5. Copia e incolla il contenuto di `FIRST_CLAUDE_CODE_PROMPT.md`.

Non chiedere a Claude di costruire subito l'intero sistema. Il prompt iniziale gli impone di partire dal Phase 0, creare la struttura minima e verificare ogni componente.

## File principali

- `CLAUDE.md` — istruzioni persistenti per Claude Code.
- `PROJECT_MANIFEST.yaml` — sintesi machine-readable del progetto.
- `docs/PROJECT_BIBLE.md` — visione completa, scope, prodotto e criteri di successo.
- `docs/ARCHITECTURE.md` — architettura target e confini tra componenti.
- `docs/DATA_MODEL_AND_METRICS.md` — schema logico, eventi, Viral Score e misure.
- `docs/RIGHTS_AND_PLATFORM_GUARDRAILS.md` — regole non negoziabili su fonti, copyright, publishing e fact checking.
- `docs/PLUGIN_STACK.md` — plugin/MCP Claude Code consigliati, divisi per fase.
- `docs/PROMPT_CONTRACTS.md` — contratti degli agenti AI del prodotto.
- `docs/ROADMAP.md` — sequenza di sviluppo e criteri di uscita da ogni fase.
- `docs/DECISIONS.md` — decisioni architetturali già prese (D009-D017 chiudono le scelte aperte di Phase 0: provider, reframe, commentary, tooling, fixture, fact check).
- `docs/SOURCES.md` — registro delle fonti autorizzate; condizione d'ingresso per la Phase 1.
- `FIRST_CLAUDE_CODE_PROMPT.md` — prompt da usare nella prima sessione.

## Sviluppo locale (Phase 0, milestone 1)

Prerequisiti: `uv`, Python 3.12+ (installato da `uv` se manca), `ffmpeg`/`ffprobe` nel PATH.

```bash
uv sync                      # crea .venv e installa il pacchetto `vme` con le dipendenze dev
uv run ruff check .          # lint
uv run mypy src              # type check strict
uv run pytest                # test (le fixture audio/video sono generate con ffmpeg -f lavfi)
```

Flusso CLI minimo (la registrazione di un file è rifiutata finché la policy non permette `ingest`):

```bash
cp .env.example .env         # opzionale: default = ./artifacts/vme.sqlite3
uv run vme source add --id S001 --uri /path/al/file.mp4 --publisher operator
uv run vme policy add --source S001 --basis owned --reference "docs/SOURCES.md#S001" \
    --can-ingest --can-extract-clip --can-transform
uv run vme media register --source S001 /path/al/file.mp4
uv run vme source show S001
uv run vme transcribe --media <media_id>          # faster-whisper locale (D011), transcript RAW immutabile
uv run vme transcript show <transcript_id> --full  # segmenti e parole con timestamp
uv run vme segment --transcript <transcript_id>    # candidati deterministici (segmenter:v0.1.0)
uv run vme candidate list --transcript <transcript_id>
```

Fixture vocali per i test di trascrizione: vedi `fixtures/speech/README.md`
(`uv run python scripts/fetch_speech_fixtures.py` scarica sample di pubblico dominio con hash
verificato in `fixtures/speech/external/`, mai committati — D019). Il primo `transcribe` scarica
il modello whisper della dimensione configurata (`VME_STT_MODEL_SIZE`) nella cache di Hugging Face.

Exit code: `0` ok, `1` errore, `2` uso errato, `3` bloccato dal rights gate. Lo stdout è sempre un documento JSON; i log strutturati (JSON per riga, con `correlation_id`) vanno su stderr.

## Filosofia di sviluppo

Il progetto deve partire come pipeline locale, osservabile e verificabile. UI, multi-tenant SaaS, pubblicazione automatica e machine learning sofisticato arrivano solo dopo aver dimostrato che la pipeline seleziona e produce clip valide in modo ripetibile.

Il sistema deve essere provider-agnostic dove economicamente sensato: Claude Code serve soprattutto a costruire e mantenere il software; in produzione si potranno usare modelli differenti per filtraggio economico, reasoning forte, trascrizione e vision.

## Nota importante

La disponibilità pubblica di un video non implica automaticamente il diritto di scaricarlo, trasformarlo o ripubblicarlo. La pipeline deve bloccare per default le fonti senza una `rights_policy` esplicita.

Data bible: 2026-09-09.
