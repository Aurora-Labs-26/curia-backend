# Curia v2 — Claude Instructions

## Change Log Rule

**Every code change made to this repository must be logged in `CHANGELOG.md`.**

This applies to all changes regardless of size: bug fixes, new features, config changes,
schema migrations, new files, deleted files. No exceptions.

### Log format

Append to the top of `CHANGELOG.md` under the current date using this structure:

```
## YYYY-MM-DD · <who made the change>

### <category>
- **`path/to/file.py`** — one-line description of what changed and why
```

`who` is the person or agent that made the change, e.g. `Bhabani`, `Claude (claude-sonnet-4-6)`,
or `Bhabani + Claude (claude-sonnet-4-6)` for a paired session.

Categories: `Bug Fix`, `Feature`, `Config`, `Migration`, `Refactor`, `Docs`, `Test`

If multiple files change as part of one logical change, group them under a single
category block with a shared description above the file list.

### Example entry

```
## 2026-05-12

### Feature
Per-episode `length_minutes` and `speaker` overrides on `POST /episodes`.
- **`api/schemas.py`** — added `length_minutes` (int, 3–30) and `speaker` fields to `CreateEpisodeRequest`
- **`api/routes/episodes.py`** — INSERT stores both new fields
- **`studio/generator.py`** — `process_episode` reads and applies both overrides
- **`alembic/versions/0008_episode_overrides.py`** — migration adding columns to episode table

### Bug Fix
- **`api/routes/sources.py`** — fixed 204 response crash (FastAPI requires `response_class=Response`)
```

---

## Project Overview

Curia v2 is an article-to-podcast generation pipeline.

**Stack:** FastAPI + asyncpg (API) · async worker polling `jobs` table · DSPy (LLM calls) ·
LangGraph (idea generation workflow) · ElevenLabs / edge-tts (TTS) · pydub + ffmpeg (audio stitching) ·
PostgreSQL + pgvector (storage + embeddings) · Alembic (migrations)

**Entry points:**
- API server: `uvicorn api.main:app --reload --port 8000`
- Worker: `python -m worker`
- CLI: `python -m studio.cli`

**Key directories:**
```
api/          FastAPI routes + schemas
core/         DB, embeddings, ingest, LLM config, TTS
studio/       Generator, briefing builder, show profiles, formats
optimization/ Rubric judge, DSPy optimization
config/       models.yaml — all LLM/TTS provider + binding config
alembic/      DB migrations
```

**Config:** All model/provider/voice configuration lives in `config/models.yaml`.
Do not hardcode model IDs, voice IDs, or API keys anywhere in source — use the config layer.

**TTS:** edge-tts is the active provider (free, no API key). Outputs MP3.
ElevenLabs is supported but requires `ELEVENLABS_API_KEY`. Stub fallback (silent WAV)
activates automatically when no key is set.

**Migrations:** Always create a new migration file in `alembic/versions/` and run
`python -m alembic upgrade head` after any schema change. Never modify existing migration files.

**Speaker overrides:** Valid speaker names are `kenji`, `arjun`, `emeka`.
Defined in `studio/shows/profiles.py`. Voice bindings in `config/models.yaml`.

---

## Test Plan

Full test plan and agent orchestration strategy: `TEST_PLAN.md`

Run results are written to: `TEST_RESULTS.md`

Before running tests confirm:
1. PostgreSQL is running: `pg_isready -h localhost -p 5432 -U curia`
2. API is up: `curl -s http://localhost:8000/health`
3. Worker is running: `python -m worker`
