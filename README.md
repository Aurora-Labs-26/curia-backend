# Curia

> **Curia turns your reading list into your own podcast.**
> You save articles. It picks the connections you didn't see, writes a host script in your preferred tone and length, narrates it, and grades the result against what you've told it you like. When it doesn't meet your bar, it tries again.
>
> Two people using the same articles get two different episodes — because you each told it different things about what makes a good listen.

---

A backend service that turns a personal reading archive into original podcast episodes.

You save articles. Curia reads them, finds thematic connections, generates an episode angle, writes a host script tuned to your taste, synthesizes audio, and judges the result against a rubric rendered from your knowledge bank. If the judge thinks it's bad, the script regenerates once.

---

## What it does

1. **Ingest** — scrape a URL, extract full text, run 7 Claude transformations (summary, metadata, key_insights, human_stakes, core_tensions, counterpoints, examples), embed chunks via Voyage, store in Postgres + pgvector.
2. **Cluster** — embed `core_tensions + counterpoints` per source, find cliques of articles with cosine similarity ≥ 0.70.
3. **Ideate** — for each cluster (and standalones), Claude generates a show idea: angle + format. KB-aware: skips ideas matching your dislikes; biases toward your active interests.
4. **Generate** — selector picks sources for the episode (KB-derived editorial direction when none given), builds a briefing packet (with KB-derived listener context), Haiku writes an outline, Sonnet writes a transcript shaped by your tone/length/ambiguity preferences.
5. **Judge** — an LLM-as-judge scores the transcript against a rubric rendered from `(company guidelines + your KB)`. If it scores below threshold, the transcript is regenerated once.
6. **Synthesize** — transcript lines are merged into paragraphs (same-speaker runs), split into TTS-sized segments, rendered through one of seven TTS providers (config-selectable, native async), stitched with gaps, optionally prepended with an intro / appended with an outro / overlaid with music, exported as MP3. Also supports real-time streaming via WebSocket.
7. **Optimize** — QA can curate trainsets, edit guidelines, run GEPA against the rubric metric, and promote optimized prompt artifacts.

---

## Architecture

```
                                 ┌──────────────┐
                  Authorization  │              │
                  ──────────────▶│  FastAPI     │ on :8000
                                 │  api/        │   stateless
                                 └──┬───────────┘
                                    │ enqueue + read
                              ┌─────▼────────┐
                              │ Postgres +   │   pgvector
                              │ pgvector     │   jobs queue (FOR UPDATE SKIP LOCKED)
                              │ docker       │
                              └─────▲────────┘
                                    │ poll + write
                                 ┌──┴───────┐
                                 │  Worker  │
                                 │ worker/  │   ingest, generate_ideas,
                                 │          │   generate_episode, optimize
                                 └──────────┘
                                    │  audio
                                    ▼
                                 ./data/audio/<id>.mp3

External APIs (real, not local):
  · LLMs: Anthropic, OpenAI, Gemini, Grok, vLLM, OpenRouter — required (at least one)
  · Embeddings: Voyage, OpenAI, Cohere, Jina, Mistral, Gemini — optional (zero-vector stub if missing)
  · TTS: ElevenLabs, OpenAI, Cartesia, Smallest.ai, edge_tts, Google Cloud — optional, swap via config
  · Auth: Firebase (optional — falls back to legacy bearer tokens)
```

Two run modes from one Docker image (`Dockerfile`): `uvicorn api.main:app` (api) and `python -m worker.main` (worker). Postgres runs as the third compose service.

For deeper architecture see [BACKEND_PLAN.md](BACKEND_PLAN.md). For where this is heading see [FUTURE_THESIS_1.md](FUTURE_THESIS_1.md) (companion direction) and [PROMPT_OPTIMIZATION.md](PROMPT_OPTIMIZATION.md) (the GEPA path).

---

## Quick start

### Pre-reqs
- Docker Desktop (or any Docker daemon)
- Python ≥3.10 (only if running tests on the host; not required for the service)

### Bring it up

```bash
# 1. Clone, then create .env from the example
cp .env.example .env
# At minimum, set ANTHROPIC_API_KEY in .env. Other keys are optional —
# missing keys fall through to stubs (zero-vector embeddings, silent WAVs).

# 2. Build images + bring up postgres + api + worker
docker compose up -d --build

# 3. Apply schema migrations (one-time per fresh DB)
docker compose exec api alembic upgrade head

# 4. Provision a user
docker compose exec api python scripts/create_user.py \
  --email you@example.com --name yourname
# Copy the printed api_token.

# 5. (Optional) provision a QA user for admin / inspection endpoints
docker compose exec api python scripts/create_user.py \
  --email qa@example.com --name qa --role qa

# 6. (Optional) onboard your knowledge bank — interactive Q&A
docker compose exec api python scripts/onboard.py --user-id <your_user_id>
```

### Sanity check
```bash
curl -s http://localhost:8000/health
# → {"status":"ok","db":true}

export TOKEN="ck_..."
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/me
```

### Run a full pipeline
```bash
TOKEN="ck_..."

# Ingest a URL (idempotent — same URL twice is a no-op)
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"url":"https://en.wikipedia.org/wiki/Memory"}' \
  http://localhost:8000/sources

# After a few articles ingest, generate ideas
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/ideas/generate
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/ideas

# Generate an episode from one of the ideas
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"show_name":"clarity_engine","show_idea_id":"<idea_id>"}' \
  http://localhost:8000/episodes

# Watch progress
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/episodes/<id>

# Download the audio when ready
curl -H "Authorization: Bearer $TOKEN" -o ep.mp3 http://localhost:8000/episodes/<id>/audio
```

Full curl walkthrough (with multi-user comparison, QA flow, admin/inspection commands) lives in [TESTING.md](TESTING.md).

---

## API surface

```
# Auth
GET    /auth/me                      current user (id, email, name, role, avatar_url)

# User-facing
GET    /health
GET    /me                           current user (id, email, name, role)
GET    /me/kb        PUT /me/kb      knowledge bank (Pydantic-validated)
GET    /me/rubric/{task}             render the judge prompt scoring my outputs

POST   /sources                      idempotent on (user_id, url) — returns existing id if dup
GET    /sources      GET /sources/:id      DELETE /sources/:id

POST   /ideas/generate
GET    /ideas        GET /ideas/:id

POST   /episodes                     {show_name, show_idea_id?, editorial_direction?, speaker?, length_minutes?}
GET    /episodes     GET /episodes/:id      GET /episodes/:id/audio

GET    /jobs/:id                     poll job status after async operations

# Streaming
WS     /ws/episodes/:id/stream?token=ck_...    real-time audio streaming via WebSocket

# QA-only (gated by users.role='qa'; regular users get 403)
GET    /admin/users
GET    /admin/users/:id  + /kb  + /rubric/:task  + /sources  + /episodes
GET    /admin/jobs   + /jobs/:id

GET    /admin/guidelines      GET /:task    PUT /:task        DB-backed; PUT live-propagates to runtime
GET    /admin/examples        POST          DELETE /:id       trainset CRUD
POST   /admin/examples/from-episode                          import an episode's inputs as an example

POST   /admin/optimization/runs                              kick off GEPA against (task, scope)
GET    /admin/optimization/runs   GET /:id
POST   /admin/optimization/runs/:id/promote                  mark artifact as active
```

---

## Configuration

Three layers, in order of how often they change:

### 1. Environment (`.env`)
Secrets, API keys, runtime knobs. Read at boot from `.env` at the repo root.

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | **Required.** Used for every LLM call. No stub fallback. |
| `VOYAGE_API_KEY` | Optional. Embeddings stub returns zero vectors if unset (cluster scores meaningless). |
| `ELEVENLABS_API_KEY` | Optional (for the default TTS provider). Stub writes silent WAVs if unset. |
| `SMALLEST_API_KEY` | Optional. Set if you switch a speaker to the `smallest` provider. |
| `GOOGLE_TTS_API_KEY` | Optional. Set if you switch a speaker to the `google_tts` provider. |
| `XAI_API_KEY` | Optional. Set if you switch a speaker to the `xai` provider (currently a stub — verify endpoint). |
| `DATABASE_URL` | Postgres connection string. Default: `postgresql://curia:curia@postgres:5432/curia` (in compose) |
| `CURIA_ENV` | `dev` / `staging` / `prod` — selects environment-scoped overrides in `config/models.yaml` |
| `CURIA_QUALITY_THRESHOLD` | Re-roll transcript if rubric judge scores below this (default `0.6`) |
| `CURIA_QUALITY_REROLL` | Set `false` to disable auto re-roll |
| `CURIA_GEPA_DRY_RUN` | Set `true` to skip the actual GEPA optimizer call (useful for testing the harness without spending money) |
| `CURIA_AUDIO_BITRATE` | MP3 bitrate for final episode (default `128k`) |
| `CURIA_STITCH_GAP_MS` | Silence between transcript lines, ms (default `400`) |

Full list in [.env.example](.env.example).

### 2. Model registry (`config/models.yaml`)
Single source of truth for every model interaction. Providers, model aliases, and bindings (per-task / per-environment / per-show / per-speaker / per-cohort / per-user).

Edit YAML to:
- Switch the transcript model from Sonnet to GPT-4
- Use a cheaper model in dev
- Switch a speaker's TTS from ElevenLabs to Smallest.ai
- Bind a different voice to an existing speaker
- Add a new provider

Validated by Pydantic on load — broken config fails on boot. See [config/README.md](config/README.md).

### 3. Quality guidelines + rubric template
- **Quality floor:** `optimization/guidelines/transcript.py` and `outline.py` (Python module fallback) + `optimization_guidelines` table (DB-backed; QA edits via `PUT /admin/guidelines/{task}` — changes propagate live).
- **Rubric template:** `optimization/rubrics/templates/{transcript,outline}.j2` (Jinja2). Combines guidelines + user KB → judge prompt.

### 4. Per-show audio assets (optional)

Each `EpisodeProfile` in `studio/shows/profiles.py` has optional fields:

| Field | What it does |
|---|---|
| `intro_audio_path` | WAV/MP3 prepended before the first transcript line (e.g. `studio/intro.wav`) |
| `outro_audio_path` | Appended after the last transcript line |
| `music_audio_path` | Looped + overlaid under the entire episode |
| `music_gain_db` | Music attenuation in dB (default `-16`; negative = quieter) |

Set the file paths on the profile, restart `api worker`, the next episode picks them up. Pre-render an intro via `python scripts/synthesize_intro.py` (writes `studio/intro.wav`).

---

## TTS providers

Seven providers wired into the adapter layer, all with native async support. Pick one per speaker via `config/models.yaml` → `bindings.speaker.<name>.model`:

| Provider | Status | Async | Streaming | Auth |
|---|---|---|---|---|
| **elevenlabs** | ✓ working | ✓ httpx.AsyncClient | ✓ WebSocket | `xi-api-key` header |
| **openai_tts** | ✓ working | ✓ httpx.AsyncClient | ✓ HTTP chunked | `Bearer` token |
| **cartesia** | ✓ working | ✓ httpx.AsyncClient | ✓ WebSocket | `X-API-Key` header |
| **smallest** | ✓ working | ✓ httpx.AsyncClient | ✓ WebSocket/SSE | `Bearer` token |
| **edge_tts** | ✓ working (free) | ✓ native asyncio | ✓ native stream | no key needed |
| **google_tts** | ✓ working | ✓ httpx.AsyncClient | ✓ gRPC streaming | `?key=` query param |
| **xai** | ⚠ stub | ✓ ready | — | `Bearer` token |

When the API key for the selected provider is missing, the adapter falls through to a 1-second silent WAV per line — pipeline still completes end-to-end. To switch a speaker to a different provider:

```yaml
# config/models.yaml — add the model alias under `models:` (uncomment the example),
# then point a speaker at it:
bindings:
  speaker:
    kenji:
      model: smallest-lightning            # was: eleven-multi-v2
      voice_id: <your_smallest_voice_id>
```

```bash
# .env — add the matching key
SMALLEST_API_KEY=...

docker compose restart api worker          # pick up env + config
```

No code changes. New episodes use the new provider.

---

## Repo layout

```
curia/
├── api/                       FastAPI service
│   ├── main.py                lifespan + router wiring
│   ├── auth.py                bearer token + role gate (current_user, qa_required)
│   ├── schemas.py             pydantic request/response models
│   └── routes/
│       ├── health.py          /health
│       ├── auth.py            /auth/me (Firebase + legacy token)
│       ├── me.py              /me, /me/kb, /me/rubric/:task
│       ├── sources.py         /sources (+ covered_in count)
│       ├── ideas.py           /ideas
│       ├── episodes.py        /episodes (+ /audio, enriched with length/speaker)
│       ├── jobs.py            /jobs/:id (polling)
│       ├── stream.py          /ws/episodes/:id/stream (WebSocket)
│       └── admin.py           /admin/* (QA only)
│
├── worker/                    long-running consumer
│   ├── main.py                dequeue → dispatch → ack/fail loop
│   └── handlers/
│       ├── ingest.py
│       ├── generate_ideas.py
│       ├── generate_episode.py
│       └── optimization.py
│
├── core/
│   ├── db/connection.py       asyncpg pool, JSONB-as-dict + pgvector codecs
│   ├── kb/                    UserKB schema + load_kb / save_kb
│   ├── llm_config/            config/models.yaml resolver + provider adapters
│   │   └── adapters/
│   │       ├── llm.py             dspy.LM construction (Anthropic/OpenAI/Gemini/Grok/vLLM/OpenRouter/Cohere)
│   │       ├── embedding.py       Voyage/OpenAI/Cohere/Jina/Mistral/Gemini + stub
│   │       └── tts.py             ElevenLabs/OpenAI/Cartesia/Smallest/edge_tts/Google/xAI + async
│   ├── audio/                 audio pipeline (shared by batch + streaming)
│   │   ├── merger.py              merge same-speaker transcript lines into paragraphs
│   │   ├── splitter.py            split paragraphs into TTS-sized segments
│   │   ├── ssml.py                SSML markup builder for batch TTS
│   │   ├── stitcher.py            prepare + synthesize + stitch segments
│   │   ├── stream.py              async generator for streaming audio
│   │   └── stream_manager.py      WebSocket streaming orchestrator
│   ├── prompts/               every LLM call as a DSPy Signature
│   │   ├── transformations.py    7 ingest extractions
│   │   ├── idea_evaluation.py    batch + single-group fallback
│   │   ├── outline.py            episode outline
│   │   ├── transcript.py         episode transcript
│   │   └── loader.py             file-based prompt override (prompts/*.txt)
│   ├── ingest.py              process_source(source_id) — worker entry
│   ├── embeddings.py          delegates to llm_config.resolve.embedder()
│   ├── tts.py                 sync + async + bytes facades
│   ├── firebase.py            Firebase Admin SDK init + token verification
│   ├── logging.py             centralized logging (terminal + JSON files)
│   ├── prompt_watcher.py      detect prompt .txt file changes
│   ├── llm_logger.py          LLM call logging decorator
│   └── queue.py               Postgres job queue (SQS-shaped)
│
├── intelligence/
│   ├── idea_generator.py      LangGraph workflow (load → cluster → eval → save)
│   └── selector.py            vector search + KB-driven editorial direction
│
├── studio/
│   ├── generator.py           process_episode pipeline; KB → briefing/transcript;
│   │                          rubric judge + auto re-roll;
│   │                          synthesize_and_stitch (intro/outro/music/bitrate/gap)
│   ├── briefing_builder.py    deterministic packet assembly (KB-aware)
│   ├── formats.py             4 format configs (narrative_drift, clarity_engine,
│   │                          momentum_loop, exploration_engine)
│   └── shows/profiles.py      speaker definitions (backstory, speech patterns,
│                              optional intro/outro/music paths)
│
├── optimization/
│   ├── guidelines/            DB-backed quality floors per task (Python fallback)
│   ├── rubrics/               (task, kb) → judge prompt;  judge() runs LLM-as-judge
│   ├── examples/              CRUD for trainset rows + import_from_episode
│   ├── runs/                  optimization_run lifecycle store
│   └── runner/                GEPA execution against the rubric metric
│
├── prompts/                   editable prompt .txt files (human + DSPy can write)
├── logs/                      structured JSON logs (curia.log, llm.log, worker.log)
├── alembic/versions/          schema migrations 0001 → 0010
├── config/models.yaml         model registry (providers, models, bindings)
├── tests/                     pytest tests (153 tests, no DB or API keys needed)
├── scripts/
│   ├── create_user.py         provision a user (--role user|qa)
│   ├── onboard.py             interactive KB Q&A
│   ├── setup_db.py            wraps `alembic upgrade head`
│   ├── ingest_urls.py         CLI ingest (legacy; API path preferred)
│   ├── run_idea_generator.py  CLI idea gen (legacy)
│   ├── run_show.py            CLI episode gen (legacy)
│   ├── synthesize_intro.py    pre-render intro WAV (writes studio/intro.wav)
│   ├── mix_episode.py         post-hoc music/intro/outro mixer (independent utility)
│   └── (others — utilities)
│
├── Dockerfile                 single image, two run modes
├── docker-compose.yml         postgres + api + worker
├── pyproject.toml             python deps + pytest config
│
├── BACKEND_PLAN.md            target architecture + AWS migration map
├── FUTURE_THESIS_1.md         companion direction (post-MVP product)
├── PROMPT_OPTIMIZATION.md     when/how to use GEPA (with caveats)
├── TESTING.md                 full curl walkthrough + multi-user QA flows
└── README.md                  this file
```

Plus runtime / generated:
```
data/audio/<episode_id>.mp3    final MP3 outputs (bind-mounted into containers)
.env                           your local secrets (gitignored)
prompts/optimized/             GEPA-compiled prompt artifacts (gitignored)
logs/                          structured logs: curia.log, llm.log, worker.log (gitignored)
```

---

## Roles

Two real roles, gated by `users.role`:

| | user | qa |
|---|------|----|
| Own profile + KB + sources + ideas + episodes | ✓ | ✓ |
| `GET /me/rubric/{task}` (own rubric) | ✓ | ✓ |
| `/admin/users` + per-user KB / rubric / sources / episodes | ✗ | ✓ |
| `/admin/jobs` operational view | ✗ | ✓ |
| `/admin/guidelines` PUT + `/admin/examples` CRUD | ✗ | ✓ |
| `/admin/optimization/runs` + promote | ✗ | ✓ |

---

## Running the tests

```bash
# Inside the running api container (no host install needed)
docker compose run --rm api bash -c \
  "pip install -q pytest pytest-asyncio && python -m pytest tests/ -v"

# Or on the host
pip install -e ".[dev]"
pytest tests/ -v
```

153 tests — schema validation, rubric rendering, config loading, briefing shape, TTS sync+async dispatch, audio merger/splitter/stitcher, WebSocket streaming, prompt file loading, Firebase auth, API schemas. **All run without a DB or any API keys.**

---

## Contributing / extending

A few load-bearing conventions worth knowing before you change things:

### "Where do I put it?" — short rule

| If you're adding... | It goes in... |
|---|---|
| A new LLM call (any kind of "ask the model X") | A new DSPy Signature in `core/prompts/`; bind it in `config/models.yaml` |
| A new model or provider | New entry under `models:` and/or `providers:` in `config/models.yaml`. For new provider type: add a branch in `core/llm_config/adapters/{llm,embedding,tts}.py` |
| A new pipeline step that runs async | New worker handler in `worker/handlers/`; a new job type in `core/queue.py:enqueue` |
| A new API route | New file under `api/routes/`; register in `api/main.py` |
| A new field in user state | Extend `core/kb/schema.py` — Pydantic catches mismatches |
| A new database table | Alembic migration in `alembic/versions/0008_*.py` |
| A new optimization metric | New function in `optimization/runner/gepa_runner.py` (currently uses rubric judge) |
| A speaker's voice change | `bindings.speaker.<name>.voice_id` in `config/models.yaml`; no code change |

### Things that are intentionally one-way

- **All LLM calls go through DSPy.** No direct `client.messages.create` anywhere in active code (verified by grep). If you're tempted to bypass DSPy, you're probably wrong.
- **All model decisions go through `config/models.yaml`.** No model strings in Python. The resolver walks `user → cohort → show → environment → task default` and returns a configured client.
- **All long-running work goes through the queue.** API endpoints enqueue and return 202; workers do the actual work. HTTP request timeouts are a real thing.
- **Auth is a single pattern.** `Depends(current_user)` for "user can do this", `Depends(qa_required)` for QA only. Don't write your own.

### Day-to-day workflow

```bash
# Pull new changes
git pull

# Rebuild + apply any new migrations
docker compose up -d --build
docker compose exec api alembic upgrade head

# Run tests after a change
docker compose run --rm api bash -c \
  "pip install -q pytest pytest-asyncio && python -m pytest tests/ -v"

# Watch logs
docker compose logs -f api worker

# Reset everything (nukes DB and audio files)
docker compose down -v
rm -rf data/audio
docker compose up -d --build
docker compose exec api alembic upgrade head
```

### Things still being figured out

- **`scripts/run_show.py`, `scripts/ingest_urls.py`, `scripts/run_idea_generator.py`** are kept for CLI use but the API path is canonical now. The scripts work but won't see the same code-path improvements.
- **GEPA promoted artifacts** — optimizer writes results back to `prompts/{task}.txt` automatically. Manual promotion of compiled artifacts still needs ~30 LOC loader.
- **xAI TTS** is a stub — raises with a clear error pointing at the YAML config field that needs setting once xAI publishes a TTS endpoint.
- **Multi-speaker episodes** — the schema + stitcher support multiple speakers, but the transcript LLM currently generates single-speaker output.
- **WebSocket streaming** — endpoint exists but frontend client not yet wired (expo-av plays files, not WebSocket streams).

---

## Where to go next

- Curl walkthrough for using it → [TESTING.md](TESTING.md)
- Editing the model registry → [config/README.md](config/README.md) and [config/models.yaml](config/models.yaml)
- Architecture deep dive → [BACKEND_PLAN.md](BACKEND_PLAN.md)
- Where Curia is heading → [FUTURE_THESIS_1.md](FUTURE_THESIS_1.md) (companion direction)
- How GEPA optimization plugs in → [PROMPT_OPTIMIZATION.md](PROMPT_OPTIMIZATION.md)

---

## License

(Set as you prefer — currently unlicensed.)
