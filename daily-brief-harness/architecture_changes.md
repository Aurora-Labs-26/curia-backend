# Architecture Changes — Two-Phase Brief Caching (Pre-Opt + Per-User Generate Brief)

Backend-only changelog for the feature built in this session: a caching architecture so article transcripts+audio (expensive: LLM + TTS) are generated once and reused across users, instead of once per user per day. Two manually-triggered flows, no cron/scheduler yet. Frontend changes (a new "Multi-User Cache" dashboard tab) are out of scope for this doc.

---

## 1. Key architecture decisions

- **New Postgres schema (`harness`), not `public`.** The existing production `db_service.py` already queries an unqualified `daily_briefs` table living in `public`. Living in a separate schema means the new tables can never collide with it by name, even against the same database.
- **`is_local` in `ARTICLE_SEGMENT_CACHE` is a redundant flag**, not an independent dimension — always mirrors `segment_type = 'local'`. Enforced by a DB `CHECK` constraint, confirmed with the user rather than assumed.
- **DB backend: local Postgres via Docker**, using raw asyncpg (no ORM/migration framework) — matches the existing `db_service.py` convention. Chosen over SQLite so the ERD's native `uuid`/`jsonb`/`point`/`enum` types could be used as-specified.
- **Two deliberate deviations from the original ERD:**
  - `USERS.location_name text` (added) — `location point` (lat/long) can't drive a Google News RSS query string; the pipeline needs a queryable place name like "Mumbai". `point` is kept, unused, for future precise use (e.g. weather).
  - `ARTICLES.topic_id` made nullable — local articles don't belong to one of the 7 topical Beats; forcing a fake "Local" topic row would conflate subject (topic) with locality (already captured by `segment_type`/`is_local`).
- **Pre-Opt's cached candidates are reused by Generate Brief, not re-fetched.** For a user's _chosen_ system topics, the per-user flow pulls Pre-Opt's already-cached top-4-per-topic candidates straight from the DB — no re-fetch, no re-heuristic-rank. Custom topics and local news are always fetched fresh (never pre-opt'd). This is what makes the cache reuse real rather than nominal.
- **New per-article segment prompts (Lead/Standard/Local) are self-contained** — no personalization, no cross-segment bridges — because a cached segment (`article_id + segment_type`) gets replayed next to arbitrary, unrelated neighbors across different users' briefs. The existing whole-brief `SYSTEM_TRANSCRIPT_PROMPT`'s `## PERSONALIZATION` / `## BETWEEN SEGMENTS` sections were deliberately not carried over.
- **TTS is never actually called this iteration.** `mp3_url` is a deterministic placeholder string (`placeholder://{kind}/{key}.mp3`); `duration_s` is estimated from word count at the harness's existing 150 WPM convention. The known `tts_service.py` logger bug was explicitly not touched (out of scope).
- **Intro/Outro are never cached** (unlike Lead/Standard/Local) — they're inherently per-user-per-day, regenerated fresh on every "Generate Brief" run, and not persisted to any DB column. Re-viewing an already-`ready` brief shows a placeholder note for these two instead of the original text.

---

## 2. New database schema (`db/`)

- **`docker-compose.yml`** — `postgres:16-alpine`, host port `5433` (avoids colliding with a default local Postgres), named volume, auto-runs `db/*.sql` on first creation.
- **`db/001_schema.sql`** — idempotent DDL. Enums: `harness.user_topic_type` (chosen/custom), `harness.segment_type` (lead/standard/local), `harness.brief_status` (generating/ready/failed). Tables:
  - `harness.users` — id, email (unique), location (point, unused), location_name, scheduled_time, created_at
  - `harness.topics` — id, name, beat, geo_gl, geo_hl, is_system — unique on `(lower(name), is_system)`
  - `harness.user_topics` (junction) — user_id, topic_id, type, is_active — unique `(user_id, topic_id)`
  - `harness.articles` — id, url, normalized_url (unique, the cross-context dedup key), title, topic_id (nullable), simhash (placeholder, unused), sig/topic_sim/composite_score (last-seen snapshot), fetched_at
  - `harness.article_segment_cache` (the key cache table) — article_id, segment_type, is_local, transcript_json, mp3_url, duration_s, generated_at — `UNIQUE(article_id, segment_type, is_local)` + `CHECK(is_local = (segment_type='local'))`
  - `harness.daily_briefs` — user_id, date, status, intro/outro/stitched_mp3_url — `UNIQUE(user_id, date)`
  - `harness.daily_brief_articles` (junction) — brief_id, article_id, cache_id (nullable), segment_type, rank (disambiguates standard-1/2/3), cache_hit — `UNIQUE(brief_id, segment_type, rank)`
- **`db/002_seed_topics.sql`** — seeds the 7 system topics (Tech, Business, World, Science & Health, Culture, Lifestyle, Sports), matching `news_service.BEATS` exactly.
- **`scripts/bootstrap_db.py`** — applies both SQL files against `DATABASE_URL`; safe to re-run (no migration framework exists to extend).
- **`scripts/seed_users.py`** — seeds 18 simulated users (varied cities/topics/schedules), with ≥2 users sharing an identical chosen-topic set and ≥2 sharing a city, guaranteed by construction so cache-hit behavior is demonstrable.

---

## 3. New backend files (`app/services/`)

| File                   | Purpose                                                                                                                                                                                                                                                           |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `harness_db.py`        | asyncpg connection pool singleton for the `harness` schema. Separate from `db_service.py`'s own pool/connections.                                                                                                                                                 |
| `cache_service.py`     | All DB-facing logic: article upsert-by-normalized-URL, segment cache get/put, user/topic lookups, daily_brief CRUD, `clear_cache()`, `display_name_for()`, `time_of_day_for()`.                                                                                   |
| `preopt_runner.py`     | Pre-Opt orchestration: per system topic, fetch 50 articles → heuristic rank (2b) → LLM score & curate (3) → full-text enrich (3b) → per-article segment transcript (6, cache-checked). Per-topic try/except isolation.                                            |
| `user_brief_runner.py` | Per-user orchestration: build pool (Pre-Opt candidates + fresh custom/local fetch) → rank/curate → resolve 5 winners against cache → generate only misses → intro/outro → assemble ordered manifest. Idempotent re-trigger via `daily_briefs` uniqueness. |

## 4. New scripts (`scripts/`)

`bootstrap_db.py`, `seed_users.py`, `__init__.py` — see §2.

## 5. Modified backend files

| File                           | What changed                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app/config.py`                | Added `DATABASE_URL` setting (defaults to the local Docker connection string). Separate from `db_service.py`'s own `os.environ["DATABASE_URL"]` read.                                                                                                                                                                                                                                                                                     |
| `.env` / `.env.example`        | Added `DATABASE_URL` line.                                                                                                                                                                                                                                                                                                                                                                                                                |
| `app/services/news_service.py` | Extracted `fetch_articles_for_beat()` and `fetch_articles_for_topic()` out of what used to be inline blocks in `fetch_articles_for_profile()` (now calls them — behavior-preserving refactor). Added public `resolve_local_geo()` wrapper around the private `_get_geo_params`.                                                                                                                                                           |
| `app/services/pipeline.py`     | Added `SEGMENT_WORD_RANGES` constant (lead 130-180 / standard 90-130 / local 70-100 words); `ArticleSegmentRequest` and `IntroOutroRequest` (with `time_of_day`) request models; `run_article_segment_step()` (Step 6, logs a warning — no retry — if word count falls outside range) and `run_intro_outro_step()` (Step 7) methods on `PipelineManager`.                                                                  |
| `app/templates/prompts.py`     | Added `SYSTEM_ARTICLE_TRANSCRIPT_LEAD/STANDARD/LOCAL_PROMPT` + shared `_ARTICLE_SEGMENT_STYLE_GUIDE` + `USER_ARTICLE_SEGMENT_PROMPT` (Step 6); `SYSTEM_INTRO_OUTRO_PROMPT` + `USER_INTRO_OUTRO_PROMPT` (Step 7) — later rewritten for a more engaging voice (persona framing, "hook" framing for the story preview, banned-phrase list) and given a `time_of_day` field mirroring `SYSTEM_INTRO_PROMPT`'s local-time grounding. Intro and the former standalone "glimpse" preview field were later merged into one combined field. |
| `app/services/llm_service.py`  | Added `MOCK_SEGMENT_LEAD/STANDARD/LOCAL` and `MOCK_INTRO_OUTRO_RESPONSE` mock constants; `call_llm()` gained a `segment_type` param (mock dispatch only); added `step_id == 6`/`== 7` branches to the mock dispatcher and to the real-call JSON-parsing condition.                                                                                                                                                                |
| `app/main.py`                  | New imports (`harness_db`, `cache_service`, `preopt_runner`, `user_brief_runner`); startup/shutdown events for the harness DB pool; 6 new endpoints (§6).                                                                                                                                                                                                                                                                                 |

---

## 6. New API endpoints

| Method & path                              | Purpose                                                                                                       |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------- |
| `POST /api/preopt/run`                     | Runs Pre-Opt across all 7 system topics.                                                                      |
| `POST /api/users/{user_id}/generate-brief` | Generates (or idempotently re-returns) one user's daily brief.                                                |
| `GET /api/harness/users`                   | Seeded users + their chosen/custom topics, for the UI picker.                                                 |
| `GET /api/harness/topics`                  | The 7 system topics.                                                                                          |
| `GET /api/harness/briefs/{brief_id}`       | Re-view an already-generated brief without re-running.                                                        |
| `POST /api/harness/clear-cache`            | Wipes `article_segment_cache` + `daily_briefs` + `daily_brief_articles`; leaves users/topics/articles intact. |

---

## 7. Notable implementation gotchas hit along the way

- **`SET search_path` via asyncpg pool `init` was unreliable** — empirically, a pooled connection's `search_path` didn't reliably survive across separate `pool.fetch()` calls, silently reverting to `public` and raising `UndefinedTableError` on the second call. Fixed by dropping the `search_path` approach entirely and schema-qualifying every table/type reference in `cache_service.py` as `harness.*` explicitly.
- **asyncpg returns `jsonb` columns as raw JSON `str`**, not auto-decoded to `dict`, with no type codec registered. `cache_service.get_cached_segment()` and `get_daily_brief_detail()` both `json.loads()` the `transcript_json` column before returning it, so callers always get a dict.
- **Full-text fetch (Step 3b) is only run on the cache-miss subset** in `user_brief_runner.py`, not on all 5 winning selections — a cache hit never needs full article body text since it's never fed to an LLM call. A deliberate optimization over a literal reading of the original spec, for strictly less work with an identical result.
- **Segment transcript text is included in every API response** (`text` field on each manifest entry) so prompt output can actually be inspected from the dashboard — this required parsing `transcript_json` (see above) and threading a `text` field through both the fresh-generation and idempotent-reuse code paths in `user_brief_runner.py`/`preopt_runner.py`.

## 8. Explicit non-goals (unchanged from original plan)

True 100-user concurrency/scale; cron/scheduler triggering; real TTS (`tts_service.py`'s logger bug is not fixed); true simhash-based fuzzy dedup (exact normalized-URL only); letting the harness UI create new custom topics/user-topic links on the fly (seed script only).

## 9. Known pre-existing issue (not introduced by this work)

`.env`'s `ANTHROPIC_API_KEY` is a non-empty placeholder string, so real LLM calls 401 instead of falling back to the harness's existing simulated-mode behavior (that fallback only triggers on an _empty_ key). Affects every LLM-backed endpoint, old and new alike — not something this feature touched or caused.
