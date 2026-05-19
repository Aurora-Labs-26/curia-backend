# Changelog

Each entry: **date · who made the change · what changed and why.**

---

## 2026-05-19 · Claude (claude-sonnet-4-6) (2)

### Bug Fix
`GET /episodes` crashed on every call — `play_progress` and `listened` columns were referenced in SQL but never added via migration, causing the Shows tab to always show empty/error.
- **`alembic/versions/0016_episode_playback_progress.py`** — adds `play_progress float` and `listened boolean NOT NULL DEFAULT false` to the `episode` table

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
