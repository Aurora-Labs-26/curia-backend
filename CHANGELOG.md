# Changelog

Each entry: **date · who made the change · what changed and why.**

---

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
