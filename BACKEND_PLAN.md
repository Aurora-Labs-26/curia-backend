# Curia Production Backend — Plan

This document captures the full plan for turning Curia from a personal-machine pipeline into a production backend. It is meant to be read end-to-end before any code changes start.

The target shape: **runs locally in Docker today, ports to AWS with mechanical changes (one file per swap), no rewrite required.**

---

## 1. What we're building

A backend service that exposes the full Curia pipeline (ingest → cluster → ideate → outline → transcript → audio) as an HTTP API, with async workers handling the slow work, and a real database underneath.

Frontend is out of scope here — it's coming later from a Figma design and will consume this API.

Two execution scenarios both supported by the same backend:
- **Reactive** — user clicks "Generate" in UI, API enqueues a job, worker runs it.
- **Scheduled** — cron fires (initially global, per-user later), enqueues jobs for users with auto-schedule on.

---

## 2. Current state of Curia (what exists today)

Curia today is a set of Python scripts that work end-to-end on a single developer machine. The pipeline logic is solid; the scaffolding around it is not production-ready.

### Modules that work and we keep
- **`core/ingest.py`** — URL → scrape (`content_core`) → 5 Claude transformations (parallel) → chunked embeddings via Ollama → primitive embedding (in a separate script). The transformations and prompts are good and we preserve them.
- **`intelligence/idea_generator.py`** — LangGraph workflow doing clustering (Python-side cosine on primitive embeddings, clique expansion) and idea evaluation via Claude Haiku. Logic is good.
- **`intelligence/selector.py`** — vector search + recent sources + insight fetch for episode brief. Logic is good.
- **`studio/briefing_builder.py`** — deterministic packet assembly. Already clean, no changes needed.
- **`studio/formats.py`** — four format configs (`narrative_drift`, `clarity_engine`, `momentum_loop`, `exploration_engine`). Good as-is.
- **`studio/generator.py`** — outline + transcript LLM orchestration. The LLM parts are good. The audio synthesis half is bad (see below).
- **`studio/shows/profiles.py`** + **`studio/shows/prompts.py`** — show profiles, speaker configs, system prompts. Keep.

### What the existing code uses
- **DB**: SurrealDB, accessed via `core/db/connection.py` (`db_query`, `db_create`, `db_update`, etc.). Schema in `core/db/migrations.py` with HNSW vector indexes on `source_embedding` and `source_primitive_embedding`.
- **Embeddings**: Ollama at `http://localhost:11434`, model `nomic-embed-text`, 768 dim.
- **TTS**: XTTS v2 invoked via subprocess against a Python 3.11 venv, with a hardcoded path to another developer's machine.
- **LLMs**: Anthropic Claude — Haiku for transformations + outlines + idea evaluation, Sonnet for transcripts.
- **Scraping**: `content-core` package.

---

## 3. What's naive about Curia today (honest audit)

A list of concrete things that block production. Each item has *what*, *why it matters*, *fix*.

### 3.1 No user model — hardcoded `user_id="default"`
- **Where**: every script and DB call.
- **Why it matters**: multi-tenant API can't exist without real users; auth has nothing to validate against.
- **Fix**: introduce a `user` table; every API request is bound to a user via the auth middleware; all queries filter by `user_id`.

### 3.2 No job/queue/status model — pipeline is fully synchronous
- **Where**: scripts call `asyncio.run(...)` to drive the whole pipeline; `embed_chunks` is fired off as `asyncio.create_task` and dies silently if the process exits.
- **Why it matters**: an HTTP request can't hold for 10+ minutes; failures vanish without trace; no way to retry.
- **Fix**: introduce a `jobs` table (Postgres-backed queue, SQS-shaped). Every long operation becomes a job with `status` (queued/running/done/failed), `attempts`, `error`. API enqueues, worker consumes.

### 3.3 No source/episode lifecycle status
- **Where**: `source` table has no `status` field; UI cannot tell "still processing" from "done with no insights".
- **Fix**: add `status TEXT` to `source` and `generation_run` (`queued | scraping | transforming | embedding | ready | failed`). Update on every state change.

### 3.4 No URL deduplication on ingest
- **Where**: `ingest_url` always creates a new row, no check for existing URL.
- **Fix**: `UNIQUE(user_id, url)` constraint; ingest is upsert-by-url.

### 3.5 Primitive embedding is a separate script (not part of ingest)
- **Where**: README says primitive embedding happens at ingest; reality is `scripts/embed_primitives.py` is run manually afterward.
- **Why it matters**: race condition — running idea generation before backfill produces empty clusters.
- **Fix**: integrate primitive embed into the ingest job; it runs after transformations, before marking `ready`.

### 3.6 Hardcoded TTS path on another machine
- **Where**: `studio/generator.py:35` — `XTTS_PYTHON = "/Users/bhabanimohapatra/..."`.
- **Why it matters**: literally cannot run for any other developer.
- **Fix**: delete XTTS path entirely. Replace with `core/tts.py` interface; default impl calls ElevenLabs HTTP API. Stub fallback for dev without an API key.

### 3.7 Two competing DB modules — `core/db/` and `core/database/`
- **Where**: `core/db/connection.py` is what's actually used; `core/database/` has migrate.py, async_migrate.py, repository.py, but isn't wired in consistently.
- **Why it matters**: confusion, dead code, easy to break things.
- **Fix**: consolidate. New layout: `core/db/` only. Anything still useful in `core/database/` migrates over; the rest gets deleted.

### 3.8 Embedding fired as fire-and-forget background task
- **Where**: `core/ingest.py:195` — `asyncio.create_task(embed_chunks(...))`. No retry, no error reporting, dies with the process.
- **Fix**: embedding becomes part of the ingest job, runs synchronously within the worker. If it fails, the job is retried with backoff.

### 3.9 Hardcoded LLM model strings
- **Where**: `claude-haiku-4-5-20251001` and `claude-sonnet-4-6` strings scattered through code.
- **Fix**: a small `core/llm.py` config that reads from env vars (`OUTLINE_MODEL`, `TRANSCRIPT_MODEL`, `TRANSFORMATION_MODEL`) with sensible defaults. Lets us upgrade models without code changes.

### 3.10 No `pyproject.toml` / requirements file
- **Where**: README has `pip install` commands inline.
- **Fix**: real `pyproject.toml` with pinned-ish deps. Single source of truth for what the Docker image installs.

### 3.11 No structured logging / no request correlation
- **Where**: `loguru.info(...)` everywhere, but no correlation between an API request and the worker job it spawned.
- **Fix**: every job carries a `correlation_id`; API stamps it into the SQS message; worker logs include it. CloudWatch becomes searchable.

### 3.12 No error handling on idea generator output
- **Where**: `parse_json_response` does `json.loads` directly; if Claude returns slightly malformed JSON, the whole batch fails.
- **Fix**: structured output via Anthropic's tool-use, or robust JSON repair. Fall back to per-group calls (already exists) but log the failure properly.

### 3.13 `covered_topic` filter is removed but table still exists
- **Where**: `intelligence/idea_generator.py:329-331` returns ideas unfiltered.
- **Fix**: decide — either drop the table, or re-enable with a real freshness check. Default for v1: drop the table; user reviews ideas in UI.

### 3.14 No support for multiple speakers in episode (XTTS-coupled)
- **Where**: README mentions Kenji/Arjun/Emeka but only Kenji has a voice; `synthesize_and_stitch` iterates speakers in transcript but everything maps to one voice in practice.
- **Fix**: ElevenLabs voice IDs are first-class in `SpeakerProfile`. Multi-speaker episodes work as soon as voices are added.

### 3.15 No tests
- **Where**: `studio/test_pipeline.py` exists but is a script, not a test.
- **Fix**: out of scope for v1. We add a smoke-test script that runs the full pipeline end-to-end against a known URL — that's the v1 acceptance gate.

---

## 4. Target architecture (local Docker, AWS-shaped)

```
docker-compose.yml
├── postgres        (pgvector/pgvector:pg16)
├── api             (FastAPI, port 8000)
├── worker          (long-running consumer)
└── (volume: ./data/audio)

When we move to AWS:
- postgres   → RDS Postgres + pgvector
- api        → AWS App Runner (single Docker image)
- worker     → ECS Fargate service (same Docker image, different command)
- volume     → S3 bucket
- queue      → SQS (replaces Postgres jobs table)
- auth       → Cognito (replaces bearer-token middleware)
- secrets    → AWS Secrets Manager (replaces .env)
- scheduler  → EventBridge → Lambda → SQS
```

**Same Docker image for API and Worker**, different entrypoint commands. This mirrors the eventual ECS Fargate split exactly.

---

## 5. The swappability layer (this is what makes AWS migration cheap)

Five abstraction interfaces. Each one is one Python module with a swappable implementation.

| Interface | Local implementation | AWS implementation | File |
|---|---|---|---|
| **Queue** | Postgres `jobs` table + `SELECT FOR UPDATE SKIP LOCKED` | `boto3.client('sqs')` | `core/queue.py` |
| **Storage** | local filesystem under `./data/audio` | `boto3.client('s3')` + presigned URLs | `core/storage.py` |
| **Auth** | bearer token compared to `DEMO_API_KEY` env var | Cognito JWT validation via JWKS | `api/auth.py` |
| **Embeddings** | Voyage HTTP API (already cloud-native) | same | `core/embeddings.py` |
| **TTS** | ElevenLabs HTTP API (already cloud-native) | same | `core/tts.py` |

When we move to AWS, we swap two of these (queue, storage, auth) and provision the AWS resources. Embeddings and TTS need no change.

---

## 6. Schema (Postgres + pgvector)

Full DDL below. Driving design rules:
- Real columns for fields we filter/sort on; `data JSONB` for messy/evolving fields.
- UUID primary keys (no Surreal `table:id` strings).
- Foreign keys with `ON DELETE CASCADE` where appropriate.
- All embeddings as `vector(N)` columns with HNSW indexes.
- All tables have `created_at`, most have `updated_at`.

```sql
-- Extensions
CREATE EXTENSION IF NOT EXISTS pgcrypto;       -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS vector;         -- pgvector

-- ----------------------------------------------------------------------
-- users
-- ----------------------------------------------------------------------
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT UNIQUE NOT NULL,
    auto_schedule   BOOLEAN DEFAULT FALSE,           -- v1: global cron, ignored
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- ----------------------------------------------------------------------
-- sources (ingested articles)
-- ----------------------------------------------------------------------
CREATE TABLE sources (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    url             TEXT NOT NULL,
    title           TEXT,
    full_text       TEXT,
    pool            TEXT DEFAULT 'user',             -- 'user' | 'system'
    status          TEXT NOT NULL DEFAULT 'queued',  -- queued|scraping|transforming|embedding|ready|failed
    error           TEXT,
    data            JSONB DEFAULT '{}'::jsonb,       -- room for future fields
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (user_id, url)
);
CREATE INDEX sources_user_status_idx   ON sources (user_id, status);
CREATE INDEX sources_user_created_idx  ON sources (user_id, created_at DESC);

-- ----------------------------------------------------------------------
-- source_insights (5 transformations per source)
-- ----------------------------------------------------------------------
CREATE TABLE source_insights (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    insight_type    TEXT NOT NULL,                   -- key_insights|human_stakes|core_tensions|counterpoints|examples
    content         TEXT,                            -- nullable: 'null' string from LLM means tier 2 absent
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (source_id, insight_type)
);

-- ----------------------------------------------------------------------
-- source_chunks + chunk embeddings
-- ----------------------------------------------------------------------
CREATE TABLE source_chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    chunk_index     INT NOT NULL,
    chunk_text      TEXT NOT NULL,
    embedding       vector(1024),                    -- Voyage default; size from env config
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (source_id, chunk_index)
);
CREATE INDEX source_chunks_embedding_idx
    ON source_chunks USING hnsw (embedding vector_cosine_ops);

-- ----------------------------------------------------------------------
-- source_primitive_embeddings (one per source — cluster signal)
-- ----------------------------------------------------------------------
CREATE TABLE source_primitive_embeddings (
    source_id       UUID PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
    embedding       vector(1024),
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX source_primitive_embeddings_idx
    ON source_primitive_embeddings USING hnsw (embedding vector_cosine_ops);

-- ----------------------------------------------------------------------
-- show_ideas (generated by idea_generator)
-- ----------------------------------------------------------------------
CREATE TABLE show_ideas (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    angle           TEXT NOT NULL,
    idea_type       TEXT NOT NULL,                   -- 'cluster' | 'standalone'
    format          TEXT NOT NULL,                   -- narrative_drift|clarity_engine|momentum_loop|exploration_engine
    source_ids      UUID[] NOT NULL,
    generated       BOOLEAN DEFAULT FALSE,           -- has been used for an episode
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX show_ideas_user_idx ON show_ideas (user_id, created_at DESC);

-- ----------------------------------------------------------------------
-- shows (show profiles — Kenji etc.)
-- ----------------------------------------------------------------------
CREATE TABLE shows (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES users(id) ON DELETE CASCADE,    -- null = system show
    name            TEXT NOT NULL,
    format_name     TEXT NOT NULL,
    config          JSONB NOT NULL,                  -- speaker config, prompts, model overrides
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (user_id, name)
);

-- ----------------------------------------------------------------------
-- generation_runs (one per episode generation attempt)
-- ----------------------------------------------------------------------
CREATE TABLE generation_runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    show_id             UUID REFERENCES shows(id),
    show_idea_id        UUID REFERENCES show_ideas(id),
    editorial_direction TEXT,
    status              TEXT NOT NULL DEFAULT 'queued',
                        -- queued|selecting|outlining|transcribing|synthesizing|ready|failed
    error               TEXT,
    title               TEXT,
    outline             JSONB,
    transcript          JSONB,
    source_ids          UUID[],
    audio_key           TEXT,                        -- storage key (local path or S3 key)
    dedup_key           TEXT,                        -- e.g. user_id:date for cron jobs
    correlation_id      UUID,                        -- traces back to API request
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE (dedup_key)
);
CREATE INDEX generation_runs_user_idx ON generation_runs (user_id, created_at DESC);

-- ----------------------------------------------------------------------
-- episodes (alias view of completed generation_runs OR separate? — go with view for simplicity)
-- ----------------------------------------------------------------------
-- We don't create a separate episode table. A "ready" generation_run IS the episode.
-- Endpoints filter by status='ready'.

-- ----------------------------------------------------------------------
-- jobs (the queue — replaceable with SQS later)
-- ----------------------------------------------------------------------
CREATE TABLE jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type            TEXT NOT NULL,                   -- ingest|generate_ideas|generate_episode
    payload         JSONB NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued',  -- queued|running|done|failed
    attempts        INT NOT NULL DEFAULT 0,
    max_attempts    INT NOT NULL DEFAULT 3,
    last_error      TEXT,
    locked_at       TIMESTAMPTZ,
    locked_by       TEXT,                            -- worker hostname
    correlation_id  UUID,
    user_id         UUID REFERENCES users(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX jobs_status_created_idx ON jobs (status, created_at)
    WHERE status IN ('queued', 'running');
```

Notes:
- We swap the Surreal `source_insight` (singular) to `source_insights` (plural) — convention.
- Embedding dimension is set to 1024 by default (Voyage-3); a config var lets us change for other providers.
- `episodes` is not a separate table — a `generation_run` with `status='ready'` IS the episode. Reduces drift.

---

## 7. Target file structure

```
curia-main/
├── BACKEND_PLAN.md                      ← this doc
├── README.md                            ← UPDATED (new setup section)
├── Dockerfile                           ← NEW
├── docker-compose.yml                   ← NEW
├── pyproject.toml                       ← NEW (replaces pip dance)
├── alembic.ini                          ← NEW
├── alembic/                             ← NEW
│   ├── env.py
│   └── versions/
│       └── 0001_init.py                 ← full schema from §6
│
├── api/                                 ← NEW
│   ├── __init__.py
│   ├── main.py                          ← FastAPI app entrypoint
│   ├── auth.py                          ← bearer-token (Cognito-swappable)
│   ├── deps.py                          ← DB pool, current user, request_id
│   ├── schemas.py                       ← pydantic request/response models
│   └── routes/
│       ├── health.py
│       ├── sources.py
│       ├── ideas.py
│       ├── episodes.py
│       └── shows.py
│
├── worker/                              ← NEW
│   ├── __init__.py
│   ├── main.py                          ← consumer loop, dispatcher
│   └── handlers/
│       ├── ingest.py                    ← wraps core/ingest.py
│       ├── generate_ideas.py            ← wraps intelligence/idea_generator.py
│       └── generate_episode.py          ← wraps studio/generator.py
│
├── core/
│   ├── db/
│   │   ├── connection.py                ← REWRITE (asyncpg + pool)
│   │   └── repository.py                ← typed CRUD helpers
│   ├── queue.py                         ← NEW (Postgres queue, SQS-shaped iface)
│   ├── storage.py                       ← NEW (local fs, S3-shaped iface)
│   ├── llm.py                           ← NEW (model name config, single Anthropic client)
│   ├── embeddings.py                    ← REWRITE (Voyage; stub fallback)
│   ├── tts.py                           ← NEW (ElevenLabs; stub fallback)
│   └── ingest.py                        ← UPDATED (DB calls, primitive embed integrated)
│
├── intelligence/
│   ├── idea_generator.py                ← UPDATED (DB calls, no Surreal)
│   └── selector.py                      ← UPDATED (DB calls, pgvector cosine)
│
├── studio/
│   ├── briefing_builder.py              ← unchanged
│   ├── formats.py                       ← unchanged
│   ├── generator.py                     ← UPDATED (DB calls, drop XTTS, use core/tts)
│   └── shows/
│       ├── profiles.py                  ← UPDATED (voice_id = ElevenLabs voice_id)
│       └── prompts.py                   ← unchanged
│
├── scripts/
│   ├── seed_demo.py                     ← NEW (creates a demo user + ingests sample URLs)
│   ├── synthesize_intro.py              ← UPDATED (ElevenLabs, not XTTS)
│   └── (delete: backfill_three_versions.py, mix_episode.py, run_*.py — replaced by API)
│
├── core/database/                       ← DELETE (replaced by core/db/)
└── data/                                ← gitignored
    └── audio/                           ← episode MP3s (local storage)
```

---

## 8. API surface (v1)

All routes prefixed with no version (`/sources`, etc.). Auth via `Authorization: Bearer <token>` header.

```
GET    /health                       liveness, no auth
GET    /me                           current user info (auth)

# Sources
POST   /sources                      enqueue ingest        body: {url}
                                     → 202 {source_id, status}
GET    /sources                      list user's archive
                                     query: ?status=ready&limit=50
GET    /sources/:id                  detail + insights
DELETE /sources/:id

# Show ideas
POST   /ideas/generate               enqueue idea generation across user's archive
                                     → 202 {job_id}
GET    /ideas                        list ideas for user
GET    /ideas/:id                    detail (sources expanded)

# Episodes (a.k.a. generation_runs)
POST   /episodes                     enqueue episode gen
                                     body: {show_id?, show_idea_id?, editorial_direction?}
                                     → 202 {generation_run_id, status}
GET    /episodes                     list user's episodes
                                     query: ?status=ready
GET    /episodes/:id                 detail (transcript, outline, status)
GET    /episodes/:id/audio           returns presigned URL or streams file

# Shows
GET    /shows                        list available show profiles (system + user)
POST   /shows                        create custom show
GET    /shows/:id

# Jobs (debug/admin — useful for the demo)
GET    /jobs/:id                     job status + last_error
```

All `POST` endpoints that enqueue jobs return `202 Accepted` with the resource ID; status moves through the lifecycle as the worker progresses.

---

## 9. Worker handlers

Three job types. Each handler is idempotent (safe to retry on crash mid-execution).

### `ingest`
- Payload: `{source_id, user_id, url}`
- Steps:
  1. `UPDATE sources SET status='scraping'`
  2. Scrape via `content_core`
  3. `UPDATE sources SET full_text, title, status='transforming'`
  4. Run 5 Claude transformations in parallel; insert into `source_insights`
  5. `UPDATE sources SET status='embedding'`
  6. Chunk text → embed each via `core/embeddings.get_embedding()` → insert into `source_chunks`
  7. Build primitive text (`core_tensions || counterpoints`); embed; insert into `source_primitive_embeddings`
  8. `UPDATE sources SET status='ready'`
- Failures: increment `attempts`, set `status='failed'` with `error` after `max_attempts`.

### `generate_ideas`
- Payload: `{user_id}`
- Steps: run the existing LangGraph workflow against new DB. Inputs/outputs unchanged otherwise.

### `generate_episode`
- Payload: `{generation_run_id}`
- Steps:
  1. Load `generation_run` row, set `status='selecting'`
  2. If `show_idea_id` given → load that idea, override sources from `source_ids`
     Else → run selector against archive
  3. Build briefing packet (existing code)
  4. `status='outlining'` → call outline LLM
  5. `status='transcribing'` → call transcript LLM
  6. `status='synthesizing'` → for each line, call `core/tts.synthesize()` → stitch with pydub → MP3
  7. Upload MP3 via `core/storage.put()` → get key
  8. `UPDATE generation_runs SET audio_key, status='ready'`
- Idempotency: if `audio_key` already set, skip synthesis.

---

## 10. Scheduler (deferred to phase 7)

Local: a cron-like Python loop in the worker that wakes once a minute and checks if the global daily run should fire. Not part of v1.

AWS: EventBridge rule fires daily 8am UTC → Lambda → Lambda queries `users WHERE auto_schedule=true` → fans out one SQS message per user with `dedup_key='{user_id}:{YYYY-MM-DD}'`. Worker picks them up. Dedup key on `generation_runs` prevents double-runs.

**v1 scope: skip the scheduler entirely.** Build the path so adding it is a 30-line Lambda later.

---

## 11. AWS migration map (when we're ready)

| Local | AWS replacement | Code changes required |
|---|---|---|
| `docker-compose: api` | App Runner from ECR image | none — same Docker image |
| `docker-compose: worker` | ECS Fargate service from same ECR image | none — same Docker image |
| `docker-compose: postgres` | RDS Postgres + pgvector extension | `DATABASE_URL` env var |
| `core/queue.py` (Postgres impl) | `core/queue.py` (SQS impl) | one file, ~60 lines |
| `core/storage.py` (local fs impl) | `core/storage.py` (S3 impl) | one file, ~40 lines |
| `api/auth.py` (bearer token) | `api/auth.py` (Cognito JWT) | one file, ~30 lines |
| `.env` | Secrets Manager + ECS task secrets | infra-side only |
| (no scheduler) | EventBridge → Lambda → SQS | new Lambda, ~30 lines |

Total app-code change to migrate: **~150 lines across 3 files.** Everything else is Terraform.

---

## 12. Implementation phases

Each phase ends with something runnable and demonstrable.

### Phase 1 — Foundation
- `Dockerfile`, `docker-compose.yml`, `pyproject.toml`
- Postgres up via compose with pgvector enabled
- Alembic configured, empty migration file
- `core/db/connection.py` rewritten with `asyncpg.Pool`
- `api/main.py` skeleton with `/health`
- **Exit criteria:** `docker-compose up`, `curl localhost:8000/health` → 200.

### Phase 2 — Schema migration
- Alembic `0001_init.py` with full DDL from §6
- `scripts/seed_demo.py` creates one demo user
- **Exit criteria:** `alembic upgrade head` succeeds; demo user row exists.

### Phase 3 — Curia core updates
- Consolidate `core/db/` and delete `core/database/`
- Add `core/llm.py` (model name config)
- Add `core/embeddings.py` Voyage impl + stub fallback
- Add `core/tts.py` ElevenLabs impl + stub fallback
- Add `core/queue.py` (Postgres impl)
- Add `core/storage.py` (local fs impl)
- **Exit criteria:** all five modules tested via standalone Python scripts.

### Phase 4 — Adapt existing pipeline modules
- `core/ingest.py`: DB calls → asyncpg, integrate primitive embed, status updates
- `intelligence/idea_generator.py`: DB calls → asyncpg, no Surreal syntax
- `intelligence/selector.py`: DB calls → asyncpg, pgvector cosine query
- `studio/generator.py`: DB calls → asyncpg, drop XTTS subprocess, call `core/tts.py`
- **Exit criteria:** the existing scripts (run as standalone) work against Postgres.

### Phase 5 — API
- Bearer auth middleware
- All routes from §8 implemented
- Job enqueue on POST endpoints
- **Exit criteria:** every route returns sensible JSON; OpenAPI at `/docs` shows the surface.

### Phase 6 — Worker
- Consumer loop with `SELECT FOR UPDATE SKIP LOCKED`
- All three handlers wired up
- Status transitions visible in DB
- **Exit criteria:** end-to-end smoke test passes (URL → ingest → ideas → episode → MP3 in `data/audio/`).

### Phase 7 — Polish
- README updated with `docker-compose up` setup instructions and curl examples
- `scripts/seed_demo.py` ingests 5 sample URLs and triggers idea generation
- Logging includes correlation IDs end-to-end

### Phase 8 (optional, separate session) — AWS migration
- Terraform for VPC, RDS, App Runner, ECS Fargate, SQS, S3, ECR, IAM, Secrets Manager
- Swap implementations of queue, storage, auth
- GitHub Actions CI: build → push to ECR → trigger deploy
- CloudWatch alarms

---

## 13. Open decisions

These are not blockers — sensible defaults are noted. Confirm or override.

1. **Voyage API key for embeddings**
   - Default: read from `VOYAGE_API_KEY` env var; if absent, stub (zero vectors). Free tier covers ~200M tokens.
2. **ElevenLabs API key for TTS**
   - Default: read from `ELEVENLABS_API_KEY` env var; if absent, stub (5-second silent MP3). Free tier covers ~10k chars/month.
3. **Embedding dimension**
   - Default: 1024 (Voyage-3). Configurable via `EMBEDDING_DIM` env var. Schema vector columns sized accordingly.
4. **Auth for v1**
   - Default: bearer token shared between API and worker, no real user signup yet. Demo user pre-seeded. Cognito wiring is Phase 8.
5. **Multi-speaker support in v1**
   - Default: ship with one speaker (Kenji equivalent on ElevenLabs). Adding more is a config change, not code.
6. **Covered topic filter**
   - Default: drop the table; user reviews ideas in UI. Re-add later if needed.
7. **Region for AWS later**
   - Default: `us-east-1`.

---

## 14. What we're explicitly NOT doing in v1

To keep scope honest:

- No Cognito / real auth (bearer token only)
- No frontend (Figma → Phase ∞)
- No scheduler (cron deferred to Phase 7+)
- No XTTS support (ElevenLabs only — XTTS needed a GPU and a separate Python env, both incompatible with our deployment shape)
- No multi-region, no read replicas, no Redis cache
- No automated tests (smoke-test script only)
- No payment/billing
- No rate limiting (add when we hit abuse)
- No background re-clustering (idea generation is on-demand only)

Each is fine to add later — none of them are load-bearing for "alpha demo."

---

## 15. Reading order for whoever picks this up

1. This doc end-to-end (you're reading it now).
2. Existing `README.md` for the pipeline conceptual model.
3. `core/ingest.py`, `intelligence/idea_generator.py`, `studio/generator.py` to see what we're preserving.
4. Then start Phase 1.
