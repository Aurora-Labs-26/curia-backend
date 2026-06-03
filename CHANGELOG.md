# Changelog

Each entry: **date · who made the change · what changed and why.**

---

## 2026-06-03 · Aditya + Claude (claude-sonnet-4-6) [2]

### Bug Fix
Two column-assignment regressions in compare UI.
- **`studio/eval_server.py`** — outline phase: only skip polling A if `_existingEp` actually has a stored outline (old episodes have none → column A was empty while B had fresh outline, making "cache" appear to be in B); transcript phase: same gate on transcript presence
- **`studio/eval_server.py`** — sidebar assignSlot: when no episode_id (outline-only run), load outline from job state via `/api/jobs/{id}` instead of silently showing nothing

---

## 2026-06-03 · Aditya + Claude (claude-sonnet-4-6)

### Feature
Editable B briefing panel in compare UI — lets user override format_config (voice style + rules) before transcript generation.
- **`studio/eval_server.py`** — "Edit B briefing ↓" toggle in phase2 bar loads B's briefing JSON into editable textarea; sends as `briefing_b` on transcript run; `_run_transcript_step` accepts `briefing_override` param; `/api/compare/run-transcript` reads and threads `briefing_b` through

---

## 2026-06-01 · Aditya + Claude (claude-sonnet-4-6) [2]

### Feature
Two-phase prompt comparison pipeline + run history storage.
- **`studio/eval_server.py`** — "Generate outlines →" runs ingest+outline only (stop_after="outline"); "Generate transcripts →" runs transcript from stored state; "Re-run outline/transcript (B)" reruns single step with edited prompt; History tab shows all past runs
- **`studio/eval_server.py`** — `compare_run` table stores every run (url, show configs, prompt overrides, job IDs, episode IDs); `GET /api/compare/runs` endpoint; `POST /api/compare/run-outline`, `run-transcript`, `rerun-step`
- **`studio/generator.py`** — `generate_outline()` and `process_episode()` accept `prompt_override` / `outline_prompt_override` for per-call DSPy Signature overrides without mutating global state
- **`core/ingest.py`** — `get_or_create_source` uses `ON CONFLICT DO NOTHING` + re-fetch to fix race condition when two jobs share the same URL
- **`pyproject.toml`** — added `groq>=0.4` and `python-multipart>=0.0.9`

---

## 2026-06-01 · Aditya + Claude (claude-sonnet-4-6)

### Feature
New `/compare` page in `studio/eval_server.py` — Substack-inspired UI with two tabs:
- **`studio/eval_server.py`** — Tab "Format A vs B": run same URL through two show formats in parallel, compare outline + transcript side-by-side
- **`studio/eval_server.py`** — Tab "vs External": upload competitor MP3 (NotebookLM etc.), transcribe via Groq Whisper, compare with Curia transcript, run LLM judge (5 axes: fidelity, naturalness, hook, coverage, narrative arc), save user verdict to `compare_feedback` table
- **`studio/eval_server.py`** — `POST /api/compare/run`, `/api/compare/transcribe`, `/api/compare/judge`, `/api/compare/feedback` endpoints
- **`pyproject.toml`** — added `groq>=0.4` for Whisper transcription

---

## 2026-05-29 · Arihant + Claude (claude-opus-4-6)

### Feature
- **`studio/eval_server.py`** — episode title is now ratable: persistent card with Good/Bad/Edit/Note above the stepper (visible on all steps); removed duplicate title card from outline step; feedback saved as `episode.title` field

---

## 2026-05-27 · Arihant + Claude (claude-opus-4-6)

### Feature
- **`studio/eval_server.py`** — title and thread now have separate Good/Bad/Edit/Note cards in the outline step (previously bundled together in a single "Overview" card)
- **`studio/eval_server.py`** — signature field on final step: text input for evaluator name, saved to new `signature` column on `eval_feedback` table, included in CSV export

### Bug Fix
- **`studio/eval_server.py`** — Submit Feedback button was invisible: CSS rule `#btn-submit { display: none }` overrode JS `style.display = ''` (clearing inline style falls back to stylesheet's `display:none`); fixed JS to set `display: 'inline-block'` explicitly
- **`studio/eval_server.py`** — cluster panel and job status were also invisible due to the same `display: ''` bug on `#cluster-panel` and `#job-status`; fixed to use `display: 'block'`
- **`studio/eval_server.py`** — pipeline errors not shown in UI: `_pollJob` error branch didn't re-enable Run button when steps existed; `submitUrl` had no try/catch for network failures; added connection-lost handling; after `process_source` now checks source status and raises a clear error if ingest failed (e.g. login-gated or blocked URLs)
- **`intelligence/clustering.py`** — `find_similar_sources` crashed on pgvector arrays: `not target_row.get(emb_col)` raises `ValueError` on numpy arrays; fixed to `is None` check
- **`intelligence/clustering.py`** — `float32` from pgvector/similarity cache not JSON-serializable; wrapped score in `float()` before returning
- **`studio/eval_server.py`** — cluster mode always re-ingested URLs because `get_or_create_source` filters by `user_id` which didn't match existing sources; now looks up source by URL first (any user), falls back to create only if none exists
- **`studio/eval_server.py`** — cluster UI fixes: panel now hides when generation starts (was staying visible); `loadCluster` shows toast on error instead of silently swallowing; `get_episode` always returns `sources` array; `buildSource` only shows multi-source in cluster mode with >1 source
- **`studio/eval_server.py`** — clustering failed for sources without core_tensions/counterpoints insights (e.g. technical docs): `embed_primitive` skipped them so they had no embedding and could never be found. Added `_ensure_primitive_embedding` that falls back to all available insights (summary, key_insights, etc.) when core_tensions/counterpoints are empty; cluster pipeline now checks for missing embeddings and generates them before searching
- **`studio/eval_server.py`** — feedback saves were silently failing: DELETE + INSERT used separate connections (each auto-committed independently) so a failed INSERT after a successful DELETE lost all data; rewrote `submit_feedback` to use a single `conn.transaction()` block for atomicity; added `_sanitize()` to strip null bytes from text fields (PostgreSQL TEXT rejects `\x00`); added post-save verification query and server-side traceback logging

---

## 2026-05-26 · Arihant + Claude (claude-opus-4-6)

### Feature
Eval server cluster mode — toggle between single-source and cluster evaluation.
- **`intelligence/clustering.py`** (new) — extracted `cosine()` similarity + `find_similar_sources()` from idea_generator; queries `source_primitive_embedding` and caches scores in `source_similarity`
- **`intelligence/idea_generator.py`** — imports `cosine` from `intelligence.clustering` instead of defining locally
- **`studio/eval_server.py`** — new Single/Cluster mode toggle in sidebar; cluster mode ingests URL then shows similar sources from DB with similarity scores, threshold slider (0.50–0.95), and checkboxes; user selects sources then generates multi-source episode; new endpoints `POST /api/find-cluster` and `POST /api/generate-from-cluster`; `_run_pipeline` accepts `mode` param and pauses at `awaiting_selection` for cluster mode; `_resume_pipeline` continues with user-selected sources; `get_episode` returns `sources` array for multi-source episodes; `buildSource` renders all sources with numbered labels

### Bug Fix
Eval server — 5 bugs fixed + minor issues + rated indicator.
- **`studio/eval_server.py`** — `_count_rated()` now uses `COUNT(DISTINCT episode_id)` instead of `COUNT(*)` (was counting rows not episodes)
- **`studio/eval_server.py`** — `get_episode` attaches all sources instead of only the first; frontend `buildSource` iterates the array
- **`studio/eval_server.py`** — removed undeclared `editMode` variable (implicit global, dead code)
- **`studio/eval_server.py`** — `selectEpisode` now calls `_saveCurrentStep()` before resetting feedback (final note was lost on episode switch)
- **`studio/eval_server.py`** — added try/catch error handling to all frontend fetch calls (`loadEpisodes`, `selectEpisode`, `submitFeedback`)

### Feature
- **`studio/eval_server.py`** — sidebar rated indicator: `/api/eval/stats` now returns `rated_ids`; sidebar shows green "Rated" badge on evaluated episodes; badge updates immediately on submit
- **`studio/eval_server.py`** — loading state shown in content area while fetching episode detail
- **`studio/eval_server.py`** — job status panel auto-clears 5s after pipeline completes
- **`studio/eval_server.py`** — full source text shown without truncation (removed `.slice(0,1200)` limit)

### Feature (earlier)
Eval server improvements — feedback dedup, persistent job state, scrollable text bodies.
- **`studio/eval_server.py`** — feedback submit now deletes previous rows for the same episode before inserting (dedup); eval pipeline job state persisted to new `eval_job` table (survives container restarts, marks stale running jobs as error on boot); added `overflow-y: auto` and `max-height` to `.eval-card-body`, `.stage-card`, and raw article preview so all text bodies scroll

---

## 2026-05-25 · Arihant + Claude (claude-opus-4-6)

### Bug Fix
Eval server feedback not saving — two issues preventing writes to `eval_feedback` table.
- **`studio/eval_server.py`** — pass `datetime` object (not ISO string) for `TIMESTAMPTZ` column; asyncpg binary protocol rejects strings
- **`studio/eval_server.py`** — default HOST to `0.0.0.0` so Railway can route traffic to the container

---

## 2026-05-24 · Aditya + Claude (claude-sonnet-4-6)

### Feature
Waitlist email capture — public `POST /waitlist` endpoint storing submissions to Railway Postgres.
- **`alembic/versions/0026_waitlist.py`** — new `waitlist` table (id, email unique, created_at, source)
- **`api/routes/waitlist.py`** — POST /waitlist; idempotent on duplicate email
- **`api/main.py`** — registered waitlist router

---

## 2026-05-23 · Aditya + Claude (claude-sonnet-4-6)

### Feature
Eval UI: per-field feedback — each insight, outline segment, and transcript gets its own inline Good/Bad + Edit + Note controls instead of one verdict per step.
- **`studio/eval_server.py`** — removed global action bar; `_makeCard()` helper builds inline verdict buttons, edit mode, and note textarea per card; `feedback.fields` flat dict keyed by field name (`source.summary`, `outline.seg.0`, `transcript`, etc.); submit serializes all fields as separate DB rows

## 2026-05-23 · Aditya + Claude (claude-sonnet-4-6)

### Feature
Eval UI: 4-step flow with Source step (scrape + 7 transformations) + live pipeline step log.
- **`studio/eval_server.py`** — step 0 "Source" added: shows article title/URL, raw content preview, and all 7 insight cards (summary, key_insights, human_stakes, core_tensions, counterpoints, examples, metadata); source step has verdict/edit/comment like outline/transcript
- **`studio/eval_server.py`** — `GET /api/episodes/{id}` now fetches source data (full_text + insights) via `source_ids[0]`; returns `episode.source` object
- **`studio/eval_server.py`** — `_run_pipeline` INSERT includes `source_ids = ARRAY[$source_id::uuid]` to link episode to ingested source
- **`studio/eval_server.py`** — live step log: `_run_pipeline` tracks 4 named steps (Looking up account → Registering source → Scraping & ingesting → Generating episode) each with done/running/error status; `_pollJob` renders step list in sidebar with ✓/↻/✗ icons

## 2026-05-23 · Aditya + Claude (claude-sonnet-4-6)

### Feature
Railway deployment config for eval server.
- **`railway.toml`** — Railway service config; builds from Dockerfile, starts `python studio/eval_server.py`, healthcheck at `/eval`
- **`studio/eval_server.py`** — feedback storage switched from ephemeral CSV to PostgreSQL `eval_feedback` table (auto-created on first submit); CSV export endpoint at `GET /eval/export.csv`; `PORT`/`HOST` env vars for Railway

### Bug Fix
Eval UI: outline segments and transcript rendering fixed for actual DB schema.
- **`studio/eval_server.py`** — `buildOutline`: render `seg.title/purpose/primitives_used` instead of `seg.focus/key_point`
- **`studio/eval_server.py`** — `buildTranscript`: continuous `pre-wrap` text block, no per-line speaker labels; `editOriginal` plain text only
- **`studio/eval_server.py`** — `buildFinal`: same continuous text block, removed per-line speaker divs

## 2026-05-23 · Aditya + Claude (claude-sonnet-4-6)

### Feature
Human eval web UI with URL ingest input and golden dataset feedback collection.
- **`studio/eval_server.py`** — FastAPI server on port 8001; `/eval` HTML UI with URL input (paste link → ingest + generate episode via `_run_pipeline`, polls `/api/jobs/{id}` every 2s, auto-selects episode when done); 3-step eval flow (Outline → Transcript → Final Transcript); per-step Good/Bad verdict buttons, inline edit mode, comment/note field; Submit saves all feedback to `data/eval_feedback.csv`

---

## 2026-05-22 · Aditya + Claude (claude-sonnet-4-6)

### Feature
Thumbnail URL extraction and storage for pile grid UI.
- **`core/scraper/cascade.py`** — `scrape()` return type extended to `tuple[str, str, str | None]`; trafilatura path extracts `meta.image`, firecrawl path extracts `ogImage`/`image` from metadata
- **`core/ingest.py`** — stores `thumbnail_url` from scrape result into source row
- **`api/schemas.py`** — `thumbnail_url: Optional[str]` added to `SourceSummary`
- **`api/routes/sources.py`** — `GET /sources` queries now include `s.thumbnail_url`
- **`alembic/versions/0025_source_thumbnail_url.py`** — migration adding `thumbnail_url TEXT` to source table

---

## 2026-05-22 · Claude (claude-sonnet-4-6)

### Feature
Cloudflare R2 audio storage — Railway has no persistent disk so generated MP3s are now uploaded to R2 after synthesis. Falls back to local disk when `CURIA_STORAGE_BACKEND` is not `s3`.
- **`core/storage/__init__.py`** — new package init
- **`core/storage/blob.py`** — S3-compatible storage abstraction (from Arihant's commit 7b5b06e); `upload_file` streams from disk; `generate_presigned_url` uses SigV4 required by R2; local fallback writes to `data/blobs/`
- **`studio/generator.py`** — after synthesis, calls `upload_file` if backend is `s3`; stores R2 key as `audio_url` on episode row; local `audio_path` kept as fallback
- **`pyproject.toml`** — added `boto3>=1.34`
- **`.env`** — documented `CURIA_S3_*` env var stubs

### Feature
Audio endpoint + `?token=` auth for Android native player.
- **`api/routes/episodes.py`** — `_audio_user_id` dependency accepts `?token=` query param (expo-av/ExoPlayer drops custom headers); R2 path returns `{"url": presigned}`; local path streams with range support; `audio_url` included in SELECT

### Bug Fix
Alembic migration chain broken — migration files 0023/0024 were lost in a revert.
- **`alembic/versions/0023_episode_source_ids.py`** — stub migration recreated to restore chain
- **`alembic/versions/0024_episode_audio_url.py`** — stub migration recreated to restore chain

---

## 2026-05-20 · Claude (claude-sonnet-4-6) (6)

### Feature
FCM push notifications: device token storage + push on episode ready.
- **`alembic/versions/0022_user_fcm_token.py`** — adds `fcm_token TEXT` column to `users` table
- **`api/routes/me.py`** — new `PUT /me/fcm-token` endpoint; saves device token for the current user; 204 response
- **`worker/handlers/generate_episode.py`** — `_notify_episode_ready()`: after episode generates, looks up user's `fcm_token` and fires FCM push ("Your show is ready"); no-ops silently if token is absent; logs warning on send failure without crashing the job

## 2026-05-20 · Claude (claude-sonnet-4-6) (5)

### Feature
Per-episode feedback: thumbs up/down + optional note, stored as JSONB on the episode row.
- **`alembic/versions/0021_episode_feedback.py`** — adds `feedback JSONB` column to episode table
- **`api/routes/episodes.py`** — new `POST /episodes/{id}/feedback` endpoint; upserts `{ rating, note }` JSON into the column; 204 response

## 2026-05-20 · Claude (claude-sonnet-4-6) (4)

### Feature
Failed sources now auto-clean instead of cluttering the pile forever. Retry is already handled by the job queue (`max_attempts=3`); this adds soft-delete + terminal-failed marking so a failed link shows once (with its reason) then disappears next session.
- **`alembic/versions/0020_source_hidden.py`** — adds `hidden boolean NOT NULL DEFAULT false` to `source` (soft-delete; rows kept for debugging)
- **`api/routes/sources.py`** — `GET /sources` (both queries) filters `hidden = false`; new `POST /sources/clear-failed` soft-hides the caller's `failed` sources (called by the client on cold-start)
- **`core/ingest.py`** — `process_source(source_id, is_final_attempt=True)`: on exception the error is always recorded, but `status='failed'` is only set on the final retry — intermediate attempts keep the in-progress status so the pile doesn't flash "Failed" between auto-retries
- **`worker/handlers/ingest.py`** — computes `is_final_attempt` from injected attempt context and passes it to `process_source`
- **`worker/main.py`** — injects `__attempt__`/`__max_attempts__` into the payload before dispatch (ephemeral); adds a throttled (hourly) safety-net that soft-hides `failed` sources older than `CURIA_FAILED_PURGE_DAYS` (default 7) for users who never open the app

## 2026-05-20 · Claude (claude-sonnet-4-6) (3)

### Bug Fix
Episodes synthesized audio successfully but failed on the generator's final write with `column "duration_seconds" of relation "episode" does not exist`. The pulled v2.2 generator writes `duration_seconds`, `description`, and `chapters` to the episode row, but no migration added those columns to this DB (schema drift).
- **`alembic/versions/0019_episode_audio_fields.py`** — new migration; adds `duration_seconds INTEGER`, `description TEXT`, `chapters JSONB` to the episode table with `IF NOT EXISTS`. Revision id kept ≤32 chars (alembic_version is varchar(32)).

## 2026-05-20 · Claude (claude-sonnet-4-6) (2)

### Bug Fix
`generate_from_source` jobs failed with `relation "source_similarity" does not exist` — the pulled `idea_generator` reads/writes a `source_similarity` cache table, but migration `0013_source_similarity` is a no-op stub (table was applied directly on the original dev DB, never created elsewhere). No shows were being created from added links as a result.
- **`alembic/versions/0018_create_source_similarity.py`** — new migration; creates `source_similarity (source_a uuid, source_b uuid, score double precision, PK(source_a, source_b))` with `IF NOT EXISTS` so it is safe on DBs where the table already exists
## 2026-05-20 · Claude (claude-sonnet-4-6) (3)

### Feature
- **`scripts/dashboard.py`** — unified real-time pipeline dashboard: persistent header with live counts + active stage spinners, scrolling event log for all components (SOURCE, JOB, CLUSTER, IDEA, EPISODE, LLM); `--llm` flag adds LLM call events, `--llm-output` adds response previews

## 2026-05-20 · Claude (claude-sonnet-4-6) (2)

### Feature
Pipeline observability — verbose cluster logging, LLM log tail, terminal test scripts for share and remix flows.
- **`intelligence/idea_generator.py`** — `cluster_sources` now logs: sources missing primitive embeddings, all above-threshold pairs with scores and titles, below-threshold pairs at TRACE level, final cluster membership with source titles
- **`core/logging.py`** — terminal log level now controlled by `CURIA_LOG_LEVEL` env var (default INFO; set DEBUG to see cluster scores and LLM outputs in terminal)
- **`scripts/tail_llm.py`** — new: pretty-prints `logs/llm.log` live; `--outputs` flag shows full LLM response text; `--all` includes historical entries
- **`scripts/test_share.py`** — new: ingest a URL + enqueue `generate_from_source` from terminal, with format/speaker/length/angle overrides
- **`scripts/test_remix.py`** — new: trigger a remix on existing source (by URL or source_id), `--list` shows recent sources

## 2026-05-20 · Claude (claude-sonnet-4-6)

### Feature
`last_played_at` column on episode — stamped on every progress update, returned in GET /episodes, powers listening history.
- **`alembic/versions/0017_episode_last_played_at.py`** — migration adding `last_played_at` (timestamptz, nullable) to episode table
- **`api/routes/episodes.py`** — UPDATE progress sets `last_played_at = NOW()`; both SELECT queries include the column
- **`api/schemas.py`** — `EpisodeSummary` exposes `last_played_at: Optional[datetime]`

---

## 2026-05-20 · Bhabani + Claude (claude-sonnet-4-6)

### Feature
Intro/outro crossfade stitching — all timings now describe the final stitched MP3, not the raw TTS body. No frontend changes required.
- **`studio/generator.py`** — replaced simple intro/outro prepend/append with pydub overlay crossfades (intro: 8s full + 5s fade-out overlapping TTS start; outro: 5s fade-in under TTS end + 3s full + 2s fade-out). Added `INTRO_FULL_MS`, `INTRO_FADE_MS`, `INTRO_GAIN_DB`, `OUTRO_FADE_IN_MS`, `OUTRO_FULL_MS`, `OUTRO_FADE_OUT_MS`, `OUTRO_GAIN_DB` constants. `tts_timings` offset is now `INTRO_FULL_MS` (when speech starts), not total intro clip length. `synthesize_and_stitch_v2` now tracks and returns `intro_offset_ms` (was missing). `_derive_display_fields` accepts `intro_ms` param, removes `round()` on chapter `startMinute`, shifts chapters 2+ by `intro_ms/60000` fractional minutes.
- **`studio/shows/profiles.py`** — updated docstring to reflect crossfade behaviour (was "prepend/append"). Added `_AUDIO_INTRO` / `_AUDIO_OUTRO` constants pointing to `assets/audio/`; wired into all four show profiles.
- **`assets/audio/intro.mp3`** — full intro music source file (full-length, backend cuts at stitch time)
- **`assets/audio/outro.mp3`** — full outro music source file (full-length, backend cuts at stitch time)

## 2026-05-19 · Claude (claude-sonnet-4-6) (2)

### Bug Fix
`GET /episodes` crashed on every call — `play_progress` and `listened` columns were referenced in SQL but never added via migration, causing the Shows tab to always show empty/error.
- **`alembic/versions/0016_episode_playback_progress.py`** — adds `play_progress float` and `listened boolean NOT NULL DEFAULT false` to the `episode` table
## 2026-05-19 · Bhabani + Claude (claude-sonnet-4-6) (6)

### Bug Fix
- **`studio/formats.py`** — added missing `resolve_format_name()` function; `POST /generate-from-source` was importing it but it didn't exist, causing a 500 on any request that included a `show_name`. Accepts both frontend slugs (`sharp-take`) and backend names (`clarity_engine`).

## 2026-05-19 · Bhabani + Claude (claude-sonnet-4-6) (5)

### Bug Fix
Share sheet was triggering a `generate_ideas` job automatically as soon as ingest finished, ignoring user customizations set in the share sheet. The ingest handler auto-enqueues `generate_ideas` after every source reaches ready — but the share sheet calls `generateFromSource` explicitly on dismiss and owns the generation step. Fix: add `auto_generate` flag to the ingest job payload; when `false`, the ingest handler skips the auto-enqueue. Share sheet now passes `auto_generate=false` via `POST /sources`.
- **`api/schemas.py`** — added `auto_generate: bool = True` to `CreateSourceRequest`
- **`api/routes/sources.py`** — threads `auto_generate` into the ingest job payload
- **`worker/handlers/ingest.py`** — skips `generate_ideas` enqueue when `auto_generate=false` in payload

## 2026-05-19 · Bhabani + Claude (claude-sonnet-4-6) (4)

### Bug Fix
`substack.com/pub/<author>/p/<slug>` URLs were returning 404 on HEAD check because that URL form isn't valid — only `<author>.substack.com/p/<slug>` works. The cascade.py `normalize_url` rewrite was correct but the worker hadn't been restarted to pick it up. Failed sources re-queued and confirmed scraping successfully.
- **`core/errors.py`** — new `PermanentError(ValueError)` exception class for failures where retrying is pointless
- **`core/queue.py`** — added `fail_permanently()` that immediately marks a job `failed` with `attempts = max_attempts` (no retry)
- **`core/scraper/cascade.py`** — validation errors, 404/403 HEAD failures, and content extraction failures now raise `PermanentError` instead of `ValueError` so the worker doesn't waste 3 attempts on dead URLs
- **`worker/main.py`** — added `except PermanentError` branch that calls `fail_permanently()` and logs `JOB_PERMANENT_FAIL`; regular transient errors still use the retry-capable `fail()`

---

## 2026-05-19 · Bhabani + Claude (claude-sonnet-4-6) (3)

### Bug Fix
`open.substack.com` URLs shared from the Substack app were failing to scrape because that domain requires login. Added rewrite in `normalise_url` to convert `open.substack.com` → `substack.com` before the URL is stored or scraped.
- **`core/ingest.py`** — `normalise_url` now rewrites `open.substack.com` to `substack.com`

### Bug Fix
edge-tts synthesis was crashing with "Cannot run the event loop while another loop is running" when called from the async worker. Fixed by running the edge-tts coroutine in a `ThreadPoolExecutor` thread with its own fresh event loop.
- **`core/llm_config/adapters/tts.py`** — `_synthesize_edge_tts` now uses `ThreadPoolExecutor` to isolate the new event loop from the worker's running loop

---

## 2026-05-19 · Bhabani + Claude (claude-sonnet-4-6)

### Bug Fix
Smallest.ai Lightning was rejecting TTS requests with "Text length exceeds the limit" — the 450-char chunking wasn't handling sentences that themselves exceed the limit, and the actual API limit is ~200 chars. Rewrote `_chunk_text` to guarantee all chunks are under 200 chars with a three-tier strategy: sentence boundaries → word boundaries → hard truncation.
- **`core/llm_config/adapters/tts.py`** — reduced `SMALLEST_MAX_CHARS` to 200; rewrote `_chunk_text` with word-level fallback and hard truncation for individual oversized words

### Feature
Job priority queue — ingest and idea-generation jobs now jump ahead of long-running episode synthesis jobs so sharing a link is never blocked by a running episode job.
- **`alembic/versions/0016_job_priority.py`** — new migration adding `priority INTEGER NOT NULL DEFAULT 10` column to `jobs`
- **`core/queue.py`** — added `_JOB_PRIORITY` map (ingest=1, generate_ideas=2, generate_from_source=3, generate_episode=10); `enqueue` now sets priority from the map; `dequeue` orders by `priority ASC, created_at ASC`

---

## 2026-05-19 · Bhabani + Claude (claude-sonnet-4-6)

### Bug Fix
`duration_seconds`, `description`, and `chapters` were null on all generated episodes because `_derive_display_fields` was lost in the v2.2 rebuild. Restored the helper and wired it into the final UPDATE in `process_episode`.
- **`studio/generator.py`** — added `_derive_display_fields(outline, duration_seconds)` helper; updated `process_episode` final UPDATE to write `duration_seconds`, `description`, and `chapters`

---

## 2026-05-19 · Bhabani + Claude (claude-sonnet-4-6)

### Feature
Restore auto-generation pipeline lost in v2.2 rebuild — ported from commit 83d01e0 (v2.1). Ingest now auto-triggers idea generation; idea generator now diffs clusters and auto-creates episodes; generate-from-source handler and route restored.
- **`worker/handlers/ingest.py`** — after source reaches ready, auto-enqueues `generate_ideas` (deduped: skips if already queued/running)
- **`intelligence/idea_generator.py`** — restored `diff_clusters` node (skips clusters already in show_idea), `auto_generate` node (creates episode rows + enqueues jobs), `auto_generated_count` state field, full graph wiring
- **`worker/handlers/generate_from_source.py`** — restored handler for share/customize flow
- **`api/routes/generate_from_source.py`** — restored `POST /generate-from-source` route
- **`worker/handlers/__init__.py`** — registered `generate_from_source` handler
- **`api/main.py`** — registered `generate_from_source` router

---

## 2026-05-19 · Bhabani + Claude (claude-sonnet-4-6)

### Config
Switch all model bindings from Anthropic to OpenAI: `gpt-4o-mini` replaces `haiku-4-5` (transformations, outline, idea evaluation), `gpt-4o` replaces `sonnet-4-6` (transcript, judge). Embedding switched from Voyage (no key, zero stubs) to OpenAI `text-embedding-3-small` (1536-dim). Backfilled all 64 existing `source_embedding` rows with real vectors.
- **`.env`** — added `OPENAI_API_KEY`
- **`config/models.yaml`** — added `openai_llm` provider; added `gpt-4o-mini` and `gpt-4o` model aliases; added `openai_embed` provider and `text-embedding-3-small` alias; updated all task/environment/show bindings

---

## 2026-05-19 · Claude (claude-sonnet-4-6)

### Feature
Cascading scraper with URL validation, HEAD check, paywall detection, and Twitter/X routing — replaces the inline trafilatura-only block in `scrape_url`; adds URL normalisation to deduplicate sources with tracking params; filters idea generator to only cluster `ready` sources.
- **`core/scraper/__init__.py`** — new package marker
- **`core/scraper/validator.py`** — regex-based URL validation (video, social, shopping, adult, search, messaging, file, private IP); paywall domain set; Twitter detection helpers
- **`core/scraper/cascade.py`** — cascading scraper: validate → HEAD check → trafilatura → firecrawl → fail, with paywall/Twitter-specific error messages
- **`core/ingest.py`** — `scrape_url` replaced with thin delegation to `core.scraper.cascade.scrape`; `normalise_url` added to strip UTM/tracking params; `get_or_create_source` calls `normalise_url` as first step
- **`intelligence/idea_generator.py`** — `load_archive` query now filters `status = 'ready'` so incomplete sources are excluded from clustering
- **`.env.example`** — documented `FIRECRAWL_API_KEY` as optional fallback scraper key

### Test
- **`tests/test_url_validator.py`** — full test suite: URL validator, HEAD check, paywall detection, Twitter detection, cascading scraper behaviour

---

## 2026-05-19 · Claude (claude-sonnet-4-6) (4)

### Bug Fix
Remixed episodes stored duplicate `source_ids` because the selector returns sources from multiple show_ideas and the ids were concatenated without deduplication. Affected episode cleaned up directly in DB.
- **`studio/generator.py`** — `_coerce_source_uuids` now deduplicates (preserves order) before returning, fixing all future episode writes
- **`api/routes/episodes.py`** — list endpoint `source_objects` builder now deduplicates by UUID, fixing display for any existing episodes with duplicate source_ids in the DB

## 2026-05-19 · Claude (claude-sonnet-4-6) (3)

### Bug Fix
Progress and listened state were never persisted — `PUT /episodes/{id}/progress` endpoint was missing entirely. Frontend was silently swallowing 404s. Also `play_progress` and `listened` were not selected in the list query so state was always `"new"` on reload.
- **`api/routes/episodes.py`** — added `PUT /episodes/{episode_id}/progress` endpoint; added `play_progress` and `listened` to both list queries
- **`api/schemas.py`** — added `play_progress: Optional[float]` and `listened: bool` to `EpisodeSummary`

## 2026-05-19 · Claude (claude-sonnet-4-6) (2)

### Feature
Option B transcript sync — per-line absolute timestamps stored alongside each episode so the frontend can highlight the current transcript line during playback (Spotify-standard approach).
- **`alembic/versions/0015_episode_tts_timings.py`** — new migration adding `tts_timings JSONB` column to `episode`
- **`alembic/versions/0013_source_similarity.py`** — stub migration re-establishing the broken Alembic chain (file was missing, data was already applied)
- **`core/llm_config/adapters/tts.py`** — added `_synthesize_smallest_with_timings`, `_async_smallest_with_timings`, `synthesize_with_timings`, and `synthesize_async_with_timings`; requests word-level timestamps from Smallest AI (`"timestamps": True`), returns them alongside audio
- **`core/tts.py`** — added `synthesize_for_speaker_with_timings` returning `(output_format, word_timings)`
- **`studio/generator.py`** — `synthesize_and_stitch` now returns `(output_path, tts_timings)`; computes absolute `start_ms`/`end_ms` per line from pydub clip durations + gap; `process_episode` stores `tts_timings` JSONB in UPDATE
- **`api/schemas.py`** — added `tts_timings: Optional[Any]` to `EpisodeDetail`
- **`api/routes/episodes.py`** — `GET /episodes/{id}` now selects `tts_timings`

### Bug Fix
`length_minutes` was never written back after generation — worker reads it as an input override but never saves the actual audio duration, leaving it NULL for all episodes and causing chapter timestamps to fall back to show-format defaults.
- **`studio/generator.py`** — after stitching, reads MP3 duration via pydub and saves `actual_length_minutes` in the final UPDATE

## 2026-05-18 · Claude (claude-sonnet-4-6) (3)

### Bug Fix
LLM was outputting `"label"` instead of `"title"` for segment chapter names despite the schema specifying `"title"`. Frontend handles existing episodes via `label ?? title` fallback; prompt patched to prevent recurrence.
- **`prompts/outline.txt`** — added explicit "Do NOT use 'label' -- use 'title'" instruction to output schema

## 2026-05-18 · Claude (claude-sonnet-4-6) (2)

### Bug Fix
Chapters were all showing startMinute=0 because `GET /episodes` list query was not returning `outline`, so the frontend had nothing to derive chapter timestamps from.
- **`api/routes/episodes.py`** — added `outline` to both list queries (with and without status filter)
- **`api/schemas.py`** — added `outline: Optional[Any] = None` to `EpisodeSummary` so it serialises through

## 2026-05-18 · Claude (claude-sonnet-4-6)

### Config
- **`config/models.yaml`** — remapped Smallest AI voices: kenji→james, arjun→george, emeka→emily

### Bug Fix
`speaker_override` was correctly reaching transcript generation but was ignored by audio synthesis — all episodes were always synthesized with the default show speaker (kenji/emily) regardless of what the user selected.
- **`studio/generator.py`** — added `speaker_override` param to `synthesize_and_stitch` and `synthesize_and_stitch_v2`; when set and valid, overrides `allowed_speakers` so the correct Smallest AI voice is used. Passed through from `process_episode` call site.

---

## 2026-05-18 · Claude (claude-sonnet-4-6)

### Bug Fix
- **`api/routes/sources.py`** — `GET /sources/{id}/episodes` was passing `str(source_id)` with `::uuid` cast to `ANY(source_ids)`, silently returning zero rows. Now passes `uuid.UUID` object directly.

### Bug Fix
Source JOIN was silently returning zero rows because asyncpg requires `uuid.UUID` objects for `uuid[]` array binding — passing strings with `::uuid[]` cast doesn't work. Fixed in both list and detail endpoints.
- **`api/routes/episodes.py`** — pass `list[uuid.UUID]` (not strings) to `ANY($ids)`, drop the `::uuid[]` cast; dedup source IDs before querying

### Feature
Source objects on episode list — `GET /episodes` now batch-fetches source domain + title for all episodes in one query.
- **`api/schemas.py`** — added `source_ids` and `source_objects` fields to `EpisodeSummary`
- **`api/routes/episodes.py`** — list endpoint now selects `source_ids`, batch-JOINs `source` table, stitches `source_objects` onto each episode row

### Feature
Source objects on episode detail — domain + title now returned alongside source IDs.
- **`api/schemas.py`** — added `EpisodeSourceObject` model (id, domain, title); added `source_objects` field to `EpisodeDetail`
- **`api/routes/episodes.py`** — `GET /episodes/{id}` now JOINs the `source` table on `source_ids`, builds `source_objects` list with domain parsed from URL and title; falls back gracefully when no sources

### Feature
- **`api/routes/sources.py`** — added `GET /sources/{source_id}/episodes` endpoint; returns all episodes whose `source_ids` array contains the given source, scoped to the authenticated user

---

## 2026-05-16 · Bhabani + Claude (claude-sonnet-4-6)

### Docs
Merged and reorganised documentation files.
- **`BACKLOG.md`** — merged with `TODO.md`; now contains all bugs, pipeline tasks, UX/audio work, test plan fixes, and open decisions in one place
- **`FUTURE.md`** — merged with `FUTURE_THESIS_1.md`; Part 1 is technical improvements (clustering options), Part 2 is the full companion thesis with architecture, phases, tools, and cost estimates
- **`TODO.md`** — deleted (merged into BACKLOG.md)
- **`FUTURE_THESIS_1.md`** — deleted (merged into FUTURE.md)

### Test
- **`scripts/test_smallest_tts.py`** — new script to verify all 3 speakers (kenji/arjun/emeka) via Smallest.ai Lightning TTS; all passed
- **`TEST_RESULTS.md`** — added to repo

---

## 2026-05-15 · Bhabani + Claude (claude-sonnet-4-6)

### Feature
Switched TTS provider from edge-tts to Smallest.ai Lightning (fixes BUG-05).
- **`config/models.yaml`** — added `smallest` provider, `smallest-lightning` model alias, updated all speaker bindings (kenji → emily, arjun → john, emeka → james)
- **`.env`** — added `SMALLEST_API_KEY`

### Docs
- **`BACKLOG.md`** — created with prioritised fix list from test run + architecture discussion

---

## 2026-05-12 · Claude (claude-sonnet-4-6) — test run

### Test
First full test run against the pipeline. Results in `TEST_RESULTS.md`.
- TC-1 (auth), TC-2 (source ingest), TC-3 (failure modes), TC-4 (KB), TC-5 (rubric), TC-6 (ideas), TC-9 (admin), TC-10 (isolation), TC-11 (edge cases) all run
- TC-7, TC-12, TC-13, TC-16 blocked at synthesis stage — edge_tts rate-limited by Microsoft (BUG-05)
- 5 bugs logged: BUG-01 through BUG-05
- **`TEST_RESULTS.md`** — created with full pass/fail results and bug log

---

## 2026-05-12 · Bhabani + Claude (claude-sonnet-4-6)

### Feature
Per-episode `length_minutes` and `speaker` overrides on `POST /episodes`.
- **`api/schemas.py`** — added `length_minutes` (int, validated 3–30) and `speaker` (str) fields to `CreateEpisodeRequest`
- **`api/routes/episodes.py`** — INSERT stores both new fields as `length_minutes` and `speaker_override`
- **`studio/generator.py`** — `process_episode` SELECTs and applies both overrides; `generate_transcript` accepts `speaker_override`, looks up speaker from `SPEAKER_PROFILES` instead of show default when set; `length_override` passed to `build_briefing_packet`
- **`alembic/versions/0008_episode_overrides.py`** — migration adding `length_minutes INTEGER` and `speaker_override TEXT` columns to the `episode` table

### Feature
edge-tts integration — free TTS with no API key required.
- **`core/llm_config/schema.py`** — added `"edge_tts"` to `ProviderType` Literal
- **`core/llm_config/adapters/tts.py`** — added `output_format` property (`"mp3"` for edge_tts, `"wav"` otherwise); added early bypass of API key check for edge_tts; added `_synthesize_edge_tts()` using `ThreadPoolExecutor` with a fresh event loop to avoid `asyncio.run()` conflict with the async worker
- **`core/tts.py`** — `synthesize_for_speaker` now returns `str` (the output format) instead of `None`
- **`studio/generator.py`** — stitcher detects MP3 vs WAV from `synthesize_line_by_speaker` return value and calls `from_mp3()` or `from_wav()` accordingly
- **`config/models.yaml`** — added `edge_tts` provider, `edge-tts` model alias, updated all speaker bindings (kenji, arjun, emeka) to use edge-tts Neural voices

### Bug Fix
- **`api/routes/sources.py`** — fixed crash on `DELETE /sources/{id}`: FastAPI requires `response_class=Response` for 204 routes; changed return to `Response(status_code=204)`
- **`api/routes/admin.py`** — same 204 fix for `DELETE /examples/{example_id}`
- **`studio/generator.py`** — fixed `AttributeError: 'function' object has no attribute 'judge'`: changed import to `from optimization.rubrics.judge import judge as _rubric_judge` and removed `.judge` from all call sites

### Docs
- **`TEST_PLAN.md`** — created full test plan (TC-1 through TC-17) with testing strategy, agent orchestration guide, dependency graph, and self-discovery snippets
- **`CLAUDE.md`** — created with changelog rule, project overview, and stack reference for any agent picking up this codebase
