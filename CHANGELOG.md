# Changelog

Each entry: **date · who made the change · what changed and why.**

---

## 2026-08-12 (2) · Arihant + Claude (claude-fable-5)

### Docs
- **`client handoff v1.md`** — the app-side contract for Aditya: offline-first
  patterns (prefetch, client_ts outbox, ETag revalidation) live on prod, the
  location-toggle contract awaiting his build, playback timings, quick
  endpoint reference

### Feature
Offline-first enablers extended to EPISODES (yesterday's batch was brief-only
— caught by Arihant):
- **`api/routes/episodes.py`** — client_ts replay guard on PUT /progress
  (stores client event time, stale replays guarded out, still 204; garbage
  ts is 422; no-ts keeps blind overwrite); ETag/304 on GET /episodes and
  GET /episodes/{id}
- Note: no episode-ready push exists to enrich — episode prefetch triggers
  on app-open/foreground list revalidation instead
- **`tests/test_episodes_offline.py`** — 7 tests; 4/4 mutants killed

---

## 2026-08-12 · Arihant + Claude (claude-fable-5)

### Feature
Offline-first (subway-ready) backend enablers — the client work is
prefetch/cache/outbox (Aditya); these three make that safe and cheap:
- **`core/notifications.py`** / **`worker/handlers/brief.py`** — brief_ready
  push now carries brief_id + date so the app can background-download the
  audio without a wake-up API round trip
- **`brief/store.py`** / **`api/routes/brief.py`** — progress replay guard:
  optional client_ts on PUT /progress; last_played_at stores CLIENT event
  time and stale queued updates are guarded out (204 "stale", not an error —
  a 404 would make the outbox retry forever). Without client_ts, old blind-
  overwrite behavior is preserved. set_daily_brief_progress is tri-state now
- **`api/routes/brief.py`** — ETag/304 on GET /brief/today so offline-first
  revalidation costs headers, not a full body
- Tests: 11 new across wiring/store-prefs; 6/6 mutants killed (guard dropped,
  stale-as-404, server-time-stored, ETag never-304s/never-changes, payload
  emptied); 3 older tests updated to the tri-state/kwargs contracts

---



## 2026-08-11 (4) · Arihant + Claude (claude-fable-5)

### Bug Fix
Scheduler jobs now hold a Postgres advisory lock — the background service
autoscales (1-6 tasks) and every task runs APScheduler, so scale-out meant
N concurrent Pre-Opt runs (Sonnet cost xN) every 6h. pg_try_advisory_lock
makes one task win per cycle; losers skip silently; the winning connection
holds the session-scoped lock for the job's duration and releases it even
on failure. Deployed via hotfix branch off the prod commit (the pending
coordinates-only location change stays undeployed until the app UI ships).
- **`worker/main.py`** — `_run_exclusive` + distinct lock ids; job bodies
  split from lock-wrapped cron entries
- **`tests/test_brief_scheduling.py`** — winner/loser/crash-release/distinct-
  id contracts; 3/3 mutants killed

### Config
- **infra** — curia-api registered for autoscaling (1-4 tasks, CPU target
  tracking at 60%), matching the workers' existing SQS-backlog policies

## 2026-08-11 (3) · Arihant + Claude (claude-fable-5)

### Refactor
Location is now DEVICE-COORDINATES ONLY — the picker/free-text city path is
removed (product decision: the app's "local news" toggle is the single
control; a typed-city path reintroduced trust problems the geocoder gate was
built to kill). NOT yet deployed — ships coordinated with the app build.
- **`api/routes/brief.py`** — PUT /preferences: coords present -> reverse-
  geocoded + device coords stored; EXPLICIT nulls -> toggle OFF, location
  cleared; omitted -> keep stored. `location_name` in requests is ignored
  (old clients can't write free text). GET /brief/cities endpoint deleted
- **`brief/cities.py`** — forward-geocode surface (search_cities /
  resolve_city / Open-Meteo) deleted; reverse_geocode (Nominatim) +
  city_query_term remain
- **Tests** — picker-contract tests replaced with toggle semantics
  (explicit-nulls clear, omitted keeps, ignored location_name); 3/3 mutants
  killed

Client changes required (for Aditya):
  1. Toggle ON: GPS fix -> PUT {latitude, longitude}. Response
     {"location_name": "..."} is the display label.
  2. Toggle OFF: PUT {"latitude": null, "longitude": null} — explicit nulls,
     not omission.
  3. Remove any city picker / GET /brief/cities usage (endpoint gone) and
     any location_name in PUT bodies (now ignored).
  4. GET /preferences still returns location_name for the label.
  5. NOTE: no manual override exists anymore — if reverse geocode names the
     wrong locality, the only recourse is re-toggling from a better fix.

---

## 2026-08-11 (2) · Arihant + Claude (claude-fable-5)

### Bug Fix
Bookend BGM was humanly inaudible — fixed -14dB relative gain put music at
-29.8 dBFS under -18 dBFS voice (found by Arihant). Now NORMALIZED to a
target level (CURIA_BRIEF_BGM_TARGET_DBFS, default -25) robust to bank-track
mastering, and the bookends get music-only room: 1.5s lead-in before the
greeting, 1.5s ring-out after the sign-off (both env-tunable).
- **`brief/audio.py`** — `_leveled()` normalization; scored-intro lead-in;
  outro tail; longer outro fade
- **`tests/test_brief_audio_timings.py`** — dBFS-target assertions for quiet
  AND loud bank tracks; lead-in/tail span math

### Feature (merged PRs)
- **PR #9** (Aditya) — FIRECRAWL_API_KEY passthrough in docker-compose
- **PR #10** (Aditya) — brief_generated / brief_generation_failed PostHog
  events (mirrors episode events; core.analytics.track signature verified)

---

## 2026-08-10 · Arihant + Claude (claude-fable-5)

### Bug Fix
Article enrichment NEVER ran — dangling `model` reference from the torch
removal (8075332, Jul 23). rank_articles passed a variable that no longer
exists into enrich_and_rescore; the fail-open try/except swallowed the
NameError on every call for 18 days, so ranking always used RSS
title/description snippets instead of real article text. Found by Arihant
while debugging a short brief.
- **`brief/enrichment.py`** — enrich_and_rescore ported to core embeddings
  (`_embed_texts`/`_cos`, batched async) — the port this call site missed;
  dead `model` param dropped; sklearn import gone
- **`brief/scoring.py`** — call site updated; stale MiniLM-era comments removed
- **`tests/test_brief_scoring.py`** — regression tests that call THROUGH the
  real seam (a NameError can no longer hide behind the fail-open catch);
  content-sensitive fake embedder after a survived mutant showed the test
  accepted any embedding input; 3/3 mutants killed

---

## 2026-08-11 · Claude (claude-sonnet-5)

### Feature
- **`brief/user_brief_runner.py`** — fires `brief_generated`/`brief_generation_failed` via `core.analytics.track`, mirroring `studio/generator.py`'s existing `episode_generated`/`episode_generation_failed` pattern exactly. Daily brief had zero analytics coverage — `ANALYTICS.md`'s plan predates the brief feature's port into this codebase entirely, so nobody extended it. `brief_generated` carries `story_count`, `total_duration_s`, `generation_time_s`, and cache hit/miss counts; `brief_generation_failed` carries `error_type`. Found while building a rollout-tracking PostHog dashboard.

## 2026-08-08 · Arihant + Claude (claude-fable-5)

### Feature
Device-GPS location path — the app's "local news" toggle: OS permission →
device coordinates → PUT /brief/preferences → server-side reverse geocode to
a verified place. Completes the location overhaul (picker for manual entry,
GPS for zero-friction) on top of PR #8's coordinate-first weather.
- **`brief/cities.py`** — `reverse_geocode(lat, lon)` (Nominatim, keyless,
  zoom=10 city granularity, city→town→village→municipality→county fallback);
  returns the SAME shape as resolve_city but keeps the DEVICE coordinates,
  deliberately more precise than any gazetteer city-center; outages raise
  (503-retry), never degrade into "not a place" (422)
- **`api/routes/brief.py`** — BriefPreferences gains paired latitude/longitude
  (range-validated, both-or-neither via model validator); GPS path fires only
  when no explicit location_name (picker correction always wins); unresolvable
  fix (open ocean) → 422 "pick manually", reverse outage → 503, nothing
  unverified ever stored
- **`tests/test_brief_cities.py`** / **`tests/test_brief_wiring.py`** — 11 new
  tests; 6/6 mutants killed (one survivor exposed the non-200-vs-exception gap
  in outage handling, now asserted); live-verified: (19.076, 72.877) →
  "Mumbai, Maharashtra, India"

### Feature (merged PRs)
- **PR #6** (Aditya) — numpy declared explicitly in pyproject
- **PR #7** (Aditya) — migration 0039 for users.apple_refresh_token/
  apple_client_id + merge fixup making it idempotent (prod already has the
  columns via the manual ALTER it formalizes)
- **PR #8** (Aditya) — configurable weather provider (OpenWeatherMap default,
  WeatherAPI fallback), coordinate-first lookups, geocoder lat/lon persisted
  to harness.users.location. Deploy note: OPENWEATHER_API_KEY not yet in
  Secrets Manager — falls back to WeatherAPI until provisioned

---

## 2026-08-08 (2) · Claude (claude-sonnet-5)

### Config
- **`docker-compose.yml`** — passes `FIRECRAWL_API_KEY` through to the `api` and `worker` services, same pattern as the other provider keys. Found while testing source ingestion locally: the scraper cascade's Firecrawl fallback tier was unreachable in local dev (`FIRECRAWL_API_KEY not set`), so any URL trafilatura couldn't parse failed permanently with no second-tier rescue.

## 2026-08-07 · Claude (claude-sonnet-5)

### Bug Fix
Worker crash-loop on a fresh build: `brief/scoring.py` does `import numpy as np`
at module level (since 2026-07-23, `8075332`), but numpy was never declared in
`pyproject.toml`'s installable dependencies — only present as a stale transitive
entry in `uv.lock` (leftover from before torch was dropped). Docker's build path
(`pip install -e ".[api]"`) never installed it, so `worker/main.py`'s eager
import chain (`worker.handlers` → `brief.preopt_runner` → `brief.pipeline` →
`brief.scoring`) crashed on every worker start — zero background jobs process
(no episode/brief generation, no source ingestion) in any fresh build,
including a real ECS Fargate deploy from this Dockerfile. The API server itself
was unaffected (routes lazy-import the same chain). Found while pulling `v3`
locally to test the geocoder/BGM/timing changes.
- **`pyproject.toml`** — added `numpy>=1.26` to `dependencies`

## 2026-08-07 (2) · Claude (claude-sonnet-5)

### Bug Fix
`delete_account` crashed with `UndefinedColumnError` on a fresh-migrated DB:
`core/account.py` and `api/routes/auth.py` both read/write
`users.apple_refresh_token` / `apple_client_id` (to revoke the Apple grant
on deletion, App Store 5.1.1(v)), but no migration ever created those
columns — only ever worked in environments where they existed via an
undocumented manual `ALTER TABLE`. Found while clearing a local test
account (`flashtester112@gmail.com`) after today's `v3` pull.
- **`alembic/versions/0039_apple_refresh_token.py`** — adds both columns,
  nullable text, matching `0009_firebase_uid`'s convention for the plain
  `users` table (not `harness.users`, which is the separate brief-prefs
  table — easy to conflate, `0038` right above this one targets that one)

## 2026-08-08 · Claude (claude-sonnet-5)

### Feature
Weather went single-provider (WeatherAPI.com only, free-text search) to
configurable-provider + coordinate-first. WeatherAPI has shown inaccurate
conditions for some users; root cause was two-fold: (1) no alternate
provider to compare against, and (2) `brief/cities.py`'s geocoder result was
narrowed to `{display, name, country_code, timezone}` before reaching
weather, discarding the lat/lon the geocoder already returned — so
`brief/weather.py` had to re-search the free-text city string against a
second, independent provider, which can resolve to a different same-named
place than the one the picker confirmed.
- **`brief/config.py`** — added `OPENWEATHER_API_KEY` and `WEATHER_PROVIDER`
  (default `"openweather"`) settings, mirroring the existing `WEATHERAPI_KEY`
  property pattern.
- **`brief/weather.py`** — rewritten as a provider-agnostic dispatcher
  (`_openweather` default, `_weatherapi` fallback) that prefers
  latitude/longitude over free-text search when the caller has coordinates;
  falls back to whichever provider has a key configured if the selected
  `WEATHER_PROVIDER`'s own key is unset. Public `get_weather_and_local_time`
  signature gained optional `latitude`/`longitude` params; return shape
  unchanged.
- **`brief/cities.py`** — `search_cities()` (and by extension
  `resolve_city()`) now also returns `latitude`/`longitude` from Open-Meteo's
  geocoder response instead of discarding them.
- **`brief/store.py`** — `create_user`/`create_user_with_topics` accept
  `latitude`/`longitude` and persist them to the existing (previously
  unpopulated) `harness.users.location` point column; `get_user` decodes it
  back into `latitude`/`longitude` keys.
- **`api/routes/brief.py`** — `PUT /brief/preferences` threads the geocoder
  match's lat/lon through to `create_user_with_topics`, and propagates the
  existing stored lat/lon on the "omitted city, keep existing" path.
- **`brief/user_brief_runner.py`** — both call sites of
  `get_weather_and_local_time` now pass the user's stored lat/lon.
- **`docker-compose.yml`** — passes through `OPENWEATHER_API_KEY` and
  `WEATHER_PROVIDER` to the `api` and `worker` services, same pattern as
  `WEATHERAPI_KEY`.
- **`.env`** (local, gitignored) — added `WEATHER_PROVIDER=openweather` and
  `OPENWEATHER_API_KEY`.
- **`tests/test_brief_wiring.py`**, **`tests/test_brief_cities.py`** —
  updated for the new lat/lon fields and provider-agnostic weather dispatch;
  added `TestOpenWeatherProvider` covering coordinate-vs-free-text
  dispatch and the key-presence fallback. Full brief suite: 127/127 passing.

No new migration — `harness.users.location` (point) already existed from
0035_brief_schema, just never populated by any code path until now.

---

## 2026-08-03 (later) · Arihant + Claude (claude-fable-5)

### Feature
Bookend BGM for the daily brief — music under the intro and outro ONLY
(bank's dedicated intro/ and outro/ folders), news segments deliberately
clean. Overlay is length-invariant (recorded chapter spans stay exact),
attenuated (CURIA_BRIEF_BGM_GAIN_DB, default -14dB) with fades, and any
bank/S3 failure ships voice-only — the episode-BGM posture.
- **`brief/audio.py`** — `_apply_bookend_bgm` reusing vibe_mix's bank
  resolution + clip fitting; `_stitch` threads segment kinds
- **`tests/test_brief_audio_timings.py`** — RMS-region assertions over real
  audio (music on bookends, silence on news, fade-out inside the file);
  6/6 mutants killed — one survivor exposed a real gap (overlong outro clip
  = fade lands past EOF = abrupt cut), now asserted

### Feature
Chapter-accurate brief audio timings — the stitched MP3 was un-seekable: the
manifest's per-segment duration_s were 150-wpm word-count estimates from the
placeholder-mp3 era, so client chapter offsets drifted 15-30s from the real
TTS pace (episodes have real tts_timings; the brief never did).
- **`brief/audio.py`** — `_stitch` now returns exact per-clip (start_ms,
  end_ms) spans (300ms lead + clip + 600ms pause, by construction); after
  upload, the REAL start_s/duration_s are written back onto the manifest's
  spoken segments (silent segments left untimed), best-effort — a failed
  write never un-ships the audio
- **`brief/store.py`** — `set_latest_manifest_segments` (updates only the
  newest transcript record for the user+date)
- **`tests/test_brief_audio_timings.py`** — new: spans asserted over REAL
  generated WAVs of known lengths, write-back contract, failure isolation;
  6/6 mutants killed (one initial survivor was a mistargeted mutation — the
  pattern hit get_latest_manifest's identical ORDER BY — retargeted + killed)

### Feature
Location correctness overhaul — IP geolocation REMOVED as the city source
(carrier IPs guess wrong too often to be silent truth), and free-text city
input closed (the "timbaktu" hole). A geocoder (Open-Meteo, keyless) is now
the only gate a city can enter through. NOT yet deployed — client-visible
contract change, ships coordinated with the app's picker UI.
- **`brief/cities.py`** — new: `search_cities` (typeahead), `resolve_city`
  (validate + normalize to canonical "City, Region, Country"; exact display
  round-trip preferred, typo-forgiving top match otherwise), fail posture =
  raise on outage, never guess
- **`brief/geo.py`** / **`tests/test_brief_geo.py`** — deleted (with the
  XFF-index config and ipapi.co dependency); dead BRIEF_GEO_OVERRIDE_CITY
  removed from docker-compose
- **`api/routes/brief.py`** — new GET /brief/cities (422 short query, 503
  outage); PUT /preferences: city omitted=keep, ""=clear, non-empty=geocoder-
  validated (422 with suggestions for unknown places, 503 on outage — an
  unverified city is never stored)
- **`alembic/versions/0038_user_location_country.py`** — users.location_country
  (geocoder ISO code); NOT applied to prod yet
- **`brief/store.py`** — persists location_country through create_user paths
- **`brief/news.py`** — `geo_for_country`: news edition from the real country
  code; legacy keyword fallback fixed from substring to token match (the old
  `"in" in loc` resolved "Berlin"/"China" to the India edition)
- **`brief/user_brief_runner.py`** — local query uses the bare city
  (`city_query_term`), not the canonical display triple; edition via stored
  country code

### Test
- **`tests/test_brief_cities.py`** — new: 14 tests (parsing, display, match
  order, outage posture, Berlin-not-India)
- **`tests/test_brief_wiring.py`** / **`test_brief_scheduling.py`** /
  **`test_brief_runner.py`** — IP-era tests replaced with the picker contract
  (normalize, 422+suggestions, 503-not-guess, keep/clear semantics, cities
  endpoint); 7/7 injected mutants killed

---

## 2026-08-03 · Arihant + Claude (claude-fable-5)

### Bug Fix
Brief routes keyed lookups on the container's UTC date while the dispatcher
and transcript records key on user-local date — off-by-one for users east of
UTC between their midnight and UTC midnight (e.g. an IST user at 1 AM asking
for "today" got yesterday's brief or a spurious eager generation).
- **`api/routes/brief.py`** — new `_user_local_date(user)` (zoneinfo, falls
  back to UTC on bad/missing tz); GET /today, GET /today/audio, and
  POST /generate all look up / create / enqueue by it
- **`tests/test_brief_wiring.py`** — 8 new tests; route assertions use a
  sentinel-patched date after two live mutants exposed that comparing
  against a live-computed IST date passes a UTC-reverted route for the
  ~18h/day the calendars agree

---

## 2026-08-03 · Aditya + Claude (claude-sonnet-5)

### Bug Fix
`GET /brief/today` always returned `status: "pending"` + the raw
`scheduled_time` whenever no `daily_briefs` row existed yet — even when that
delivery time had already passed for today (e.g. a brand-new signup at 4pm,
default 9am slot already behind). The widget showed a stale "Ready at 9:00
AM" and the user then had to wait up to 15 minutes for the next dispatch
poll to actually pick them up. Every brand-new user hits exactly this branch
on day one, since they have zero `daily_briefs` rows.
- **`brief/store.py`** — added `is_user_due_now(user_id)`, the same
  timezone-aware condition as `list_due_users_for_generation`'s WHERE
  clause, scoped to one user.
- **`api/routes/brief.py`** — `get_today` now checks `is_user_due_now` in the
  "no row yet" branch; if due, creates the `daily_briefs` row and enqueues
  `generate_brief` (interactive lane) right there instead of returning
  `"pending"`. `get_or_create_daily_brief`'s `(user_id, date)` uniqueness
  means this only enqueues once — a concurrent or later call finds the row
  already created and skips straight to it. Response naturally becomes
  `status: "generating"`, which the client already renders as "Putting
  together today's stories…" — no frontend change needed.
- **`tests/test_brief_wiring.py`** — updated the existing "not due" pending
  test to stub `is_user_due_now=False` explicitly (it was auto-mocking
  truthy and would otherwise always take the new eager-trigger branch);
  added coverage for the eager-trigger-and-enqueue case and the
  no-double-enqueue-when-row-already-exists case.
- **`tests/test_brief_scheduling.py`** — added `TestIsUserDueNow` covering
  true/false/user-not-found.

## 2026-08-03 · Aditya + Claude (claude-sonnet-5)

### Config
App copy renamed "Pile" → "Stash" earlier; generated show audio still said
"Pile" since the intro-section instruction that drives host dialogue was
untouched. Audited every prompt/template file in the repo for literal
"pile" — only these two instruct the LLM to reference it by name; the
remaining hits (`core/ingest.py`, `studio/formats.py`, `api/routes/sources.py`,
`worker/handlers/ingest.py`, `alembic/versions/0020_source_hidden.py`) are
internal code comments, never sent to the model.
- **`prompts/transcript.txt`** — `THE PILE` → `THE STASH` section label
- **`prompts/transcript_merge.txt`** — same rename

## 2026-08-03 · Aditya + Claude (claude-sonnet-5)

### Bug Fix
`POST /brief/preferences` rejected `timezone: "Asia/Calcutta"` (`unknown IANA
timezone`) even though it's a real, if deprecated, IANA alias for
`Asia/Kolkata` — some Android devices still return it from
`Intl.DateTimeFormat().resolvedOptions().timeZone`. Root cause: the image had
no explicit `tzdata` package; `/usr/share/zoneinfo` only existed as an
incidental transitive apt dependency, whose data lacked the IANA "backward"
compatibility links (deprecated aliases). Canonical zones (`Asia/Kolkata`,
`UTC`, etc.) resolved fine, only legacy aliases failed.
- **`pyproject.toml`** — added `tzdata>=2024.1` as an explicit dependency,
  which ships the full official IANA tz database including backward-compat
  aliases; Python's `zoneinfo` prefers this pip package over system data.

## 2026-07-31 · Arihant + Claude (claude-fable-5)

### Feature
Merged Aditya's `v3.1` (fast-forward, 9 commits: scheduling, delivery push,
prefs UX, IP-geo city, playback progress, brief audio route, missing
`googlenewsdecoder` dependency — see `v3.1 review v1.md`), then closed the
three gaps the review flagged, TDD'd with 5 new tests (3 mutants killed):
- **`api/routes/brief.py`** — `BriefPreferences.timezone` now validated as a
  real IANA zone (§4.1: one garbage row made `list_due_users_for_generation`
  throw for ALL users every poll)
- **`worker/main.py`** — brief cron jobs gated behind `CURIA_BRIEF_JOBS`
  (§4.3: both worker services run the scheduler; set =0 on all but one or
  Pre-Opt runs twice and dispatch races)
- **`brief/store.py`** — failed briefs retry only for 2h after first attempt
  (created_at bound, §4.2: permanent failures were re-enqueuing ~96×/day)
- **`tests/test_brief_scheduling.py`** — gap tests; Aditya's failed-retry
  assertion refined to the bounded-window contract
- **`uv.lock`** — regenerated for `googlenewsdecoder` + `edge-tts`

---

## 2026-07-30 · Aditya + Claude (claude-sonnet-5)

### Bug Fix
Episode source author never reached the frontend, even though it's correctly
extracted and stored on `source.author` at scrape time (`core/scraper/cascade.py`
→ `core/ingest.py::process_source`). All three places that build
`EpisodeSourceObject` for the API response queried `SELECT id, url, title FROM
source` — never selecting `author` — and the schema itself didn't even declare
an `author` field, so it would've been silently dropped even if selected.
- **`api/schemas.py`** — added `author: Optional[str] = None` to `EpisodeSourceObject`
- **`api/routes/episodes.py`** — both `source_objects` builders (list endpoint
  and detail endpoint) now select `author` and pass it through
- **`api/routes/sources.py`** — same fix, third occurrence of the identical pattern
- Restarted `api` (no `--reload` in this compose setup) to pick up the change;
  full test suite re-run clean (1061 passed, same 6 pre-existing unrelated failures)

## 2026-07-29 · Aditya + Claude (claude-sonnet-5)

### Bug Fix
No voice output on either episode or brief playback — audio routes returned
200/206 with valid-looking responses, but the served files were silent stub
audio. Two independent regressions, both introduced by the same day's earlier
`googlenewsdecoder` rebuild (`docker compose build api worker`), both the same
pattern as that fix: a real runtime dependency/config that had only ever been
patched into a *previous* container instance by hand, silently wiped by the
rebuild.
- **`pyproject.toml`** — `edge-tts` (the active, free TTS binding for episodes
  per `config/models.yaml`) was never declared as a dependency; added
  `edge-tts>=6.1`. Confirmed via direct synthesis test post-fix (15KB of real
  audio for a short sentence, not a silent stub).
- **`docker-compose.yml`** — `CARTESIA_API_KEY` (briefs' TTS provider) was
  correctly set in `.env` but never whitelisted into either the `api` or
  `worker` service's `environment:` block (only `ELEVENLABS_API_KEY` was).
  Added `CARTESIA_API_KEY: ${CARTESIA_API_KEY:-}` to both, matching the
  existing pattern.
- Rebuilt both images and recreated both containers; confirmed the key now
  reaches the worker's env and `edge_tts` imports and synthesizes correctly.

## 2026-07-29 · Aditya + Claude (claude-opus-5)

### Bug Fix
Brief source favicons all showed news.google.com instead of the real publisher —
root cause was `googlenewsdecoder`, the package `brief/enrichment.py` lazy-imports
to decode a Google News redirect URL to its real publisher URL, was never declared
as a dependency, so every decode attempt threw `ModuleNotFoundError` (silently
caught, logged as `decode_or_fetch_error`), `resolved_url` stayed NULL for every
article, and the frontend's `resolved_url || url` fallback correctly rendered the
Google News link. Confirmed fixed live: `_decode_and_fetch_text` now resolves a
real publisher URL (e.g. thehindu.com) and extracts article text.
- **`pyproject.toml`** — added `googlenewsdecoder>=0.1.7` to `dependencies`
- rebuilt and restarted both `api` and `worker` images so the new dependency is
  actually installed (both share this image; only `worker` calls the decode path,
  but `api` failed the same import check pre-rebuild)

Also found, not fixed (out of scope — different pipeline stage): `brief/scoring.py:571`
passes a bare `model` to `enrichment_service.enrich_and_rescore` that is never
defined in `rank_articles`'s scope, raising `NameError` on every call. The
surrounding `try/except` swallows it and logs "Article enrichment step failed,
continuing without it" — meaning the ranking accuracy-refinement rescore step has
never actually run since it was wired up. Needs its own investigation into what
`model` should be before fixing.

### Refactor
Product rename: "Daily Brief" → "Daily Roundup" in user-reaching copy only.
Routes, table names, job types, and identifiers are all deliberately untouched
— renaming those would need a migration and would break already-installed app
builds for no user-visible gain.
- **`core/notifications.py`** — push title "Your daily brief is ready" → "Your daily roundup is ready"
- **`brief/prompts/article_transcript_local.py`**, **`article_transcript_standard.py`** — "Curia Daily Brief" → "Curia Daily Roundup" in the scriptwriter framing
- **`brief/prompts/article_segment_style_guide.py`**, **`article_transcript_lead.py`**, **`intro_outro.py`**, **`score_curate.py`** — "briefing"/"daily brief" → "roundup" where the word names the product. Left alone where "brief" means *short* (intro_outro's "brief close", the style guide's "keep them brief") — those are voice instructions, not product references

## 2026-07-23 · Arihant + Claude (claude-fable-5)

### Feature
Publisher-URL resolution hardening + headline-only judge calibration — the two
fixes the first live faithfulness data pointed at (3/5 critical flags, all
traceable to failed enrichment putting the judge in its strictest posture).
- **`brief/enrichment.py`** — per-stage failure logging (decode_failed /
  fetch_blocked / extract_empty); NEW last-resort cascade on the Google News
  URL itself when the decode fails (Jina/Firecrawl render the JS redirect —
  previously a decode outage sent everything headline-only); `_try_one` now
  returns (text, resolved_publisher_url)
- **`brief/pipeline.py`** — resolved_url threaded onto fetched selections
- **`brief/store.py`** — `set_article_resolved_url` (identity `url` untouched);
  brief detail now selects `resolved_url` (real publisher links in the API)
- **`brief/user_brief_runner.py`** / **`brief/preopt_runner.py`** — persist the
  resolved URL after enrichment in both generation paths
- **`alembic/versions/0036_article_resolved_url.py`** — adds the column
- **`brief/faithfulness.py`** — headline-only segments: judge once, never retry
  (a rewrite can't cure a flag with no source text — observed live); new
  "unverifiable" severity for plausible-but-uncheckable details beyond the
  headline, ranked below moderate, never triggers regeneration; "critical"
  in headline-only mode reserved for contradictions

### Test
- **`tests/test_brief_enrichment.py`** — new: 13 tests over the 4-hop rescue
  chain, resolved_url threading, store SQL contract, migration chain
- **`tests/test_brief_faithfulness.py`** — headline-only no-retry contract,
  unverifiable ranking + prompt; 6 injected mutants all killed

### Feature
Inline faithfulness gate for daily-brief segments — the parked harness judge,
un-parked and slimmed. Per cache-miss segment: generate → judge (source-
comparison, no web tool) → if critical/moderate, regenerate once with the
flagged claims threaded via prior_text/prior_feedback → judge again → ship
attempt 2 regardless (the brief never blocks). Judge failures fail open.
- **`brief/faithfulness.py`** — new: judge prompt (critical/moderate/stylistic
  taxonomy, headline-only mode when content_fetched=False), `judge_segment`,
  `generate_verified_segment` (2-attempt cap), verdict attached to the result
- **`brief/user_brief_runner.py`** — swapped parked stub for the real gate;
  memoizes the shipped verdict onto the cache row AFTER put (put nulls memo
  columns); "unknown" verdicts never memoized
- **`brief/preopt_runner.py`** — cache-filling segments now judged the same way
- **`config/models.yaml`** / **`brief/llm.py`** — new `brief.judge` binding
  (haiku-4-5) + "judge" registered in BRIEF_STEPS
- **`tests/test_brief_faithfulness.py`** — 14 tests incl. the double-flag
  ship-attempt-2 contract and fail-open; 7 injected mutants all killed
  (retry-never-fires, verdict-whitewash, crash-propagation, memo-on-unknown, …)

### Test
GAN-style hardening of the brief package: generator round wrote 46 new tests,
then a discriminator round injected 18 targeted source mutants (wrong lead
index, truncation off-by-one, inverted cache gate, dropped failure-marking,
flipped created-flag, weather accepting errors, audio gate inverted, …) and
verified every one is killed by at least one test. 2 initial survivors
exposed real assertion gaps (regen-path status, mutation pattern) — hardened.
- **`tests/test_brief_runner.py`** — new: 23 assembly-logic tests for
  `generate_brief_for_user` + `_regenerate_bookends_for_brief` via a fully
  wired mock Rig (manifest order, lead/standard/local typing, rank counters,
  cache hit/miss economics, selection truncation, local-query construction,
  failure marking, ready-brief short-circuit). Runner coverage 11% → 97%
- **`tests/test_brief_store.py`** — pure-helper edge sweep (normalize_url,
  durations, _to_time/_to_date, simhash, display-name precedence) +
  get_or_create_daily_brief created-flag contract + user-topics split
- **`tests/test_brief_wiring.py`** — weather degradation matrix (no key, no
  location, non-200, network exception → always ("","")). Weather 24% → 100%

### Bug Fix
- **`brief/audio.py`** — audio pass always skipped ("not ready"): it read
  `detail["status"]` but `get_daily_brief_detail` returns a nested
  `{"brief": {...}, "articles": [...]}` shape; now reads the brief row.
  Test doubles corrected to the real shape (`tests/test_brief_wiring.py`)

### Bug Fix
- **`brief/store.py`** — first live generate_brief failed with "the JSON object
  must be str... not dict": the ported reads json.loads'd jsonb columns, but
  core/db's codec already auto-decodes them. New tolerant `_jsonb()` helper
  applied at all five read sites (writes were safe — the encoder passes
  pre-dumped strings through)
- **`tests/test_brief_store.py`** — regression tests: `_jsonb` passthrough for
  dict/list/str/None, `get_cached_segment` with codec-decoded transcript_json

### Config
All TTS moved to Cartesia on the latest model generation (episodes + daily brief).
- **`config/models.yaml`** — `cartesia-sonic` alias bumped `sonic-2` → `sonic-3.5`
  (current generation, July 2026); speaker bindings kenji/arjun/emeka switched
  smallest-lightning → cartesia-sonic with three distinct library voices
  (Theo / Ronald / Lauren); previous smallest bindings kept as comments
- **`core/llm_config/adapters/tts.py`** — `Cartesia-Version` header bumped
  `2024-06-10` → `2026-03-01` (required by the sonic-3.x API) in both sync and
  async paths
- **`tests/test_tts_cartesia.py`** — asserts sonic-3.5, the new version header,
  and that all three speakers are Cartesia-bound with distinct voice ids; verified
  live against the API (all three voices synthesize)

### Feature
Daily-brief integration, wiring layer (Tasks 4–5 of the harness port): worker
lanes, API routes, and real TTS audio — replacing the harness's sync-HTTP
orchestration and `placeholder://` mp3 stubs.
- **`worker/handlers/brief.py`** — new handlers `preopt_brief` (background lane,
  optional `topic_id`) and `generate_brief` (interactive lane; runs
  `generate_brief_for_user` then a best-effort audio pass that never fails the job)
- **`worker/handlers/__init__.py`** — registered both handlers in `HANDLERS`
- **`api/routes/brief.py`** — new router: GET `/brief/topics`, PUT
  `/brief/preferences`, POST `/brief/generate` (409 without prefs, 202 + job id),
  GET `/brief/today`, POST `/brief/preopt`
- **`api/main.py`** — registered the brief router
- **`brief/audio.py`** — `render_brief_audio(brief_id)`: manifest texts →
  `resolve.tts()` per segment → pydub stitch with 600 ms pauses → S3 upload at
  `audio/brief/{id}.mp3` → `set_daily_brief_audio`; skips non-ready briefs,
  failures return None without touching the DB
- **`brief/store.py`** — port additions `set_daily_brief_audio` (stitched-url-only
  UPDATE) and `get_latest_manifest` (ordered segment texts incl. intro/outro from
  `transcript_records`, the only place bookend text is persisted)

### Test
- **`tests/test_brief_wiring.py`** — 21 new tests: handler registry + payload
  routing, audio-failure isolation, route auth/validation/enqueue-lane contracts,
  audio happy path + never-un-ready guarantee, store helper SQL contracts
- **`tests/test_imports.py`** — worker HANDLERS set now includes the two brief types

### Docs
- **`dailybrief analysis v1.md`** — deep analysis of `feat/dailyBrief_v3`
  (Sourajit's daily-brief-harness: Google-News → curated cached segments →
  faithfulness-gated brief scripts + a 5-judge eval harness with gold-set
  calibration). Full function map, code critique (0 tests, hardcoded models,
  CORS/auth issues, sync 54s judge calls in request path), overlap matrix vs
  curia systems, and 3 integration options — recommended: staged assimilation
  (config+DB first, worker/TTS later).

### Config
Tension graph populated + calibrated on prod: stance backfill (271 sources →
240 links, 31 honest "none", 0 failures) + topics backfill (290 sources, the
pre-deploy corpus — wildcard/bucket logic was blind without it). SNAP_THRESHOLD
recalibrated 0.85 → 0.72 after the initial registry fragmented completely
(0 intra-user shared tensions; measured same-question pairs cluster 0.70-0.84);
9 fragment groups merged (12 rows). Result: Connect live with 5 connectable
seeds incl. a genuine antagonist pair (pro/anti algorithmic curation) found in
the corpus. Product note: antagonists are structurally scarce in bookmark
corpora (people save what they agree with) — wildcards are the workhorse role,
and the 0.62-0.72 "related tension" band is the designed next expansion tier.
- **`core/tension/registry.py`** — SNAP_THRESHOLD 0.72 with calibration notes

### Feature
**Connect** — multi-source episodes via the tension graph (replaces the removed
clustering; design: "companion selection research v1.md", validated by
"stance card samples v1.md" — 86% clean domain-free extraction, 4% leak rate).
- **`core/prompts/transformations.py`** — `stance` card absorbs core_tensions +
  counterpoints (ingest 4 → 3 transformation calls): canonical domain-free
  tension + polarity + confidence + domain phrasing + steelmanned counter in
  one extraction
- **`core/ingest.py`** — stance post-processing: derives legacy
  core_tensions/counterpoints insight rows (briefing arm F unchanged), embeds
  the canonical tension, snap-or-creates the registry row, links with polarity
- **`core/tension/`** (new) — `registry.py` (snap threshold 0.85, exact
  tension_id matching thereafter) + `connect.py` (cast: antagonist = same
  tension/opposite polarity; wildcard = same tension/different bucket; depth =
  same bucket; ≤4; abstains without contrast or wildcard; ONE LLM call
  validates the cast + writes the episode angle, with template fallback)
- **`alembic/versions/0034_tension_registry.py`** — `tension` (dual embedding
  columns per the 0010 scheme, HNSW), `source_tension` (polarity, confidence),
  `episode.selection_plan` JSONB
- **`api/routes/sources.py`** — `POST /sources/{id}/connect` (rides the
  existing show_idea → generate_episode pipeline; 409 on abstain) + precomputed
  `connectable` flag on source lists; **`api/schemas.py`** — SourceSummary.connectable
- **`config/models.yaml`** — `transformation.stance`, `connect.angle` bindings
- **`tests/test_tension_connect.py`** (new, 15) — registry snap/create, role
  classification, assembly priority, abstention, cap, validate+angle fallbacks
- Suite: 889 passing, known-baseline failures unchanged. Backfill deliberately
  NOT run (per decision) — the graph populates from new ingests; run a backfill
  script later to connect the existing corpus. Migration 0034 pending prod.

## 2026-07-22 · Arihant + Claude (claude-fable-5)

### Feature
Briefing switch (ablation arm F): the packet now carries each source's **full
clean_text as `article_text`** (uncapped — deliberate policy decision; the
ablation measured at 14k chars) plus core_tensions + counterpoints only. One
batched text fetch in the generator keeps every source-selection path complete;
legacy rows prettified on the fly. Outline prompts rewritten to hook from the
article's own claims.
- **`studio/briefing_builder.py`** — `article_text` in primitives; PRIMITIVE_FIELDS → tensions+counterpoints; `texts` param
- **`studio/generator.py`** — batched clean_text/full_text fetch before packet build
- **`core/prompts/outline.py`** + **`prompts/outline.txt`** — article-text-first guidance

### Refactor
`key_insights` transformation removed (ingest 5 → 4 transformation calls; 8 → 5
total LLM calls per source across this week's changes). Ablation: inert alone
(0.56), zero marginal value on top of tensions+counterpoints. Episode
eligibility re-gated on `summary`.
- **`core/prompts/transformations.py`**, **`core/prompts/__init__.py`**, **`prompts/extract_key_insights.txt`** (deleted), **`config/models.yaml`**
- **`intelligence/idea_generator.py`** — `has_complete_insights` gates on summary; group formatting uses summary
- **`intelligence/selector.py`**, **`api/routes/eval.py`**, **`studio/run_episode_gen.py`** — field lists pruned

### Refactor
Clustering removed entirely. `cluster_sources` now yields one standalone
candidate per source — the cosine/greedy-clique machinery (0.61 threshold,
max-size 5, `source_similarity` cache) optimized "which docs are similar",
the wrong objective per "companion selection research v1.md". Its replacement
is the Connect tension-graph selector. `embed_primitive` (its only feeder)
removed from ingest — one less embedding call per source. Tables untouched.
- **`intelligence/idea_generator.py`** — singleton clusters; clique code deleted
- **`core/ingest.py`** — `embed_primitive` call + function removed
- **`tests/test_embed_primitive.py`** deleted; **`tests/test_ingest_pipeline.py`**, **`tests/test_briefing.py`**, **`tests/test_llm_config.py`** updated
- Suite: 867 passing, known-baseline failures unchanged

## 2026-07-20 · Arihant + Claude (claude-fable-5)

### Docs
- **`ablation results v1.md`** — transformation ablation study: 8 prod sources ×
  9 briefing arms × position-swapped pairwise LLM judging (64 transcripts, 56
  comparisons, ~$13). Headline: the current primitives-only briefing lost to
  plain clean_text 0.25 (zero wins) — the revamp's briefing-switch thesis
  confirmed experimentally. Best arm: clean_text + core_tensions + counterpoints
  (0.62, never lost). key_insights and summary measured inert in the briefing.
  Adopted: switch briefing to arm F; remove key_insights (ingest 5→4 calls);
  summary demoted to blurb-only.

## 2026-07-19 · Arihant + Claude (claude-fable-5)

### Refactor
Removed the `human_stakes` and `examples` transformations — ingest drops from 7 to
5 LLM calls per source (~29% cheaper). Rationale ("companion selection research
v1.md" + revamp v1): `examples` was rarely useful and often hallucinated with no
consumer in the new architecture; `human_stakes` only seasoned a briefing that is
moving to clean_text, where the transcript LLM derives stakes itself. Kept:
summary, metadata, key_insights, core_tensions, counterpoints (tensions/counterpoints
pending the stance-card rework). Old `source_insight` rows for the removed types
remain harmlessly; the outline prompt no longer references the dead primitives.
- **`core/prompts/transformations.py`** — signatures, module wiring, TRANSFORMATION_NAMES, TIER_2
- **`core/prompts/__init__.py`** — re-exports + docstring
- **`prompts/extract_examples.txt`**, **`prompts/extract_human_stakes.txt`** — deleted
- **`config/models.yaml`** — two transformation bindings removed
- **`studio/briefing_builder.py`**, **`intelligence/selector.py`**, **`intelligence/idea_generator.py`** — field lists pruned
- **`core/prompts/outline.py`** + **`prompts/outline.txt`** — prompt guidance no longer cites dead primitives
- **`api/routes/eval.py`** — transform list + UI cards down to 5
- **`studio/run_episode_gen.py`** — sample briefing updated
- **`tests/test_briefing.py`**, **`tests/test_llm_config.py`** — assertions updated; suite 877 passing, baseline unchanged

## 2026-07-16 · Arihant + Claude (claude-fable-5)

### Docs
- **`companion selection research v1.md`** — deep-research synthesis (30 sources,
  124 claims, 11 triple-verified) on companion-document selection to replace the
  removed clustering: MMR/DPP/submodular limits (DPPs can't encode positive
  complementarity), complementary-rec transfer via LLM-labeled relation taxonomy +
  distillation, weakest-link narrative coherence, setwise LLM prompting economics,
  NotebookLM/PodAgent product evidence. Concludes with 3 ranked architectures;
  recommended: bucket-aware candidates → LLM pair-relation typing (cached in a
  future `source_relation` table) → role-coverage set assembly.

### Config
- BGM bank: `intro/` + `outro/` vibe clips uploaded to
  `s3://curia-audio/assets/bgm_bank/` (restores intro/outro sound lost in the
  vibe-mix port — the old jingles now play as ducked beds under the spoken
  intro/outro). Zero code change; workers rolling-restarted to re-sync.

> Companion doc: **`CHANGETHOUGHT.md`** holds the design rationale, AWS-migration plan,
> frontend↔backend contract, and decision log. This file stays the per-file ledger.

---

## 2026-07-13 · Arihant + Claude (claude-fable-5)

### Bug Fix
YouTube ingest: a blocked transcript request (YouTube rejects datacenter IPs —
confirmed live from prod's NAT) raised a non-permanent exception, so SQS retried
it uselessly and the source card spun on "scraping" forever (same class as the
perpetual-Queued bug fixed in 176db3c). `RequestBlocked` now raises
`PermanentError` → clean "failed" state with a user-readable message. Note: YT
ingestion remains non-functional from AWS until a proxy is configured
(`YOUTUBE_USE_TOR` exists; rotating-residential proxy is the robust option).
- **`core/scraper/youtube.py`** — catch `RequestBlocked` (incl. `IpBlocked`) → `PermanentError`
- **`tests/test_youtube_scraper.py`** — blocked-IP → permanent test

### Config
BGM bank shipped to prod and verified end-to-end: episode "Staying Upwind" mixed with
music in 8/8 transition pauses (−27 dBFS in pauses, ducked ~12 dB under voice).
Bank uploaded via one-shot GitHub Actions workflow (473MB local uplink was
impractical); scoped IAM user's access key deleted after use.
- **`infra/RUNBOOK.md`** — worker task role gained `s3:ListBucket` on `curia-audio`
  prefix-scoped to `assets/*` (vibe-mix bank sync lists the prefix; Get/Put alone
  → AccessDenied; first episode shipped silent via the designed fallback)
- **`.github/workflows/bgm-sync.yml`** (on `main`) — reusable manual sync
  feat/bgm-sfx `assets/` → `s3://curia-audio/assets/`

## 2026-07-13 · s0radummy + Arihant + Claude (claude-fable-5)

### Feature
Per-segment vibe BGM + transition SFX (integrated from `feat/bgm-sfx`, code-only
cherry-pick of `29f7bff` by s0radummy). Every outline segment gets a vibe tag
(8 vibes; outline LLM assigns, `DEFAULT_VIBE=curious` fallback); one bank clip per
segment crossfaded across segment pauses, sidechain-ducked under voice (ffmpeg),
plus a transition SFX at each boundary. Degrades to voice-only on any asset/mix
failure. Replaces the single-track music system.
- **`core/audio/vibe_mix.py`** (new) — segment bounds, BGM/SFX layers, sidechain, `build_vibe_mix()`
- **`studio/generator.py`** — emits tts_timings + segment_transitions; calls the mixer; drops old music bed
- **`studio/formats.py`** — VIBE_DEFINITIONS, intro/outro sentinels, SEGMENT_PAUSE_MS, SFX_PAD_MS
- **`core/prompts/outline.py` + `prompts/*.txt`** — per-segment vibe assignment in outline; transcript prompts aligned
- **`studio/shows/profiles.py`** — per-show music config removed; **`studio/test_music_bed.py`** deleted
- **`api/schemas.py` / `api/routes/episodes.py`** — expose `episode.bgm_plan` (QA)

**Integration decisions (differ from the branch):**
- **BGM bank (473MB of mp3s) is NOT in git or the image** — it lives at
  `s3://curia-audio/assets/bgm_bank/` (+ `assets/sfx/`). `vibe_mix` resolves:
  `$CURIA_BGM_DIR` → repo `assets/` → one-time S3 sync to `$CURIA_BGM_CACHE_DIR`.
  Music is swappable without a redeploy; worker role already had S3 read.
- Migration renumbered **0029_episode_bgm_plan → 0033** (collided with
  0029_source_og_image; prod already stamped at 0032).
- **`tests/test_vibe_mix.py`** (new) — 18 tests: bounds math (intro/outro ordering
  trap), bleed/crossfade overlap, clip fallback, full S3 resolution chain (mocked)
- **`.env.example`** — CURIA_BGM_DIR / CURIA_BGM_S3_BUCKET / CURIA_BGM_CACHE_DIR / CURIA_SFX_PATH
- **`pyproject.toml`** — `pytest-asyncio` moved into the dev dependency-group
  (plain `uv sync` had pruned it, breaking collection)

## 2026-07-13 · Arihant + Claude (claude-fable-5)

### Feature
Prettifier layer — rule-based clean-text pass on every article after scraping,
before ANY LLM consumer (revamp v1 "Layer 1"; no LLM involved). Fixes encoding
(entities, mojibake, zero-width chars), strips short boilerplate lines
(subscribe/cookie/share patterns), dedups scraper double-grabbed paragraphs,
normalizes whitespace; safety valve returns the original if >70% was removed.
Transformations, chunk embeddings, and the topics text-signal now all read
`clean_text or full_text`; raw `full_text` kept for audit.
- **`core/scraper/prettify.py`** (new) — `prettify()`, deterministic and idempotent
- **`core/ingest.py`** — prettify on scrape + store `clean_text`; backfill-on-touch for pre-prettifier rows; `text_for_llm` feeds transformations/embeddings/topics
- **`alembic/versions/0032_source_clean_text.py`** — adds `source.clean_text`
- **`tests/test_prettify.py`** (new) — 14 tests incl. over-strip valve + idempotence
- **`tests/test_ingest_pipeline.py`** — insight-INSERT assertion made position-independent (clean_text backfill UPDATE may precede it)

### Feature
Jina Reader as scrape tier 2 (trafilatura → jina → firecrawl) — hosted
URL→LLM-ready-markdown extraction (r.jina.ai, non-generative). Key-gated:
without `JINA_API_KEY` the cascade behaves exactly as before. Cuts firecrawl
spend on JS-heavy pages.
- **`core/scraper/cascade.py`** — `_scrape_jina()` + gated tier in `scrape()`
- **`tests/test_jina_reader.py`** (new) — parsing, auth header, cascade ordering, key-gating
- **`.env.example`** — documented `JINA_API_KEY`, `JINA_READER_URL`

### Bug Fix
Cartesia streams WAV with placeholder RIFF/data sizes (0xFFFFFFFF) — ffmpeg/pydub
tolerate it, but Python `wave` consumers misread (a 9.5s sample claimed 27 hours).
Found via live e2e sampling; the adapter now repairs the header after download.
- **`core/llm_config/adapters/tts.py`** — `_fix_streamed_wav_header()` applied in both `_synthesize_cartesia` and `_async_cartesia`
- **`tests/test_tts_cartesia.py`** — repair, no-op-on-wellformed, no-op-on-non-WAV, end-to-end synth cases

### Feature
Sarvam AI (Bulbul) + Cartesia (Sonic) TTS adaptability. Sarvam is a new adapter:
chunked ≤`max_chars` at sentence boundaries, base64-WAV responses concatenated at
the PCM frame level, sync + native-async paths, `target_language_code` setting for
Indian languages. Cartesia's existing synth method is now wired to active config.
Speaker bindings unchanged (smallest) — switching a voice is a models.yaml edit.
- **`core/llm_config/adapters/tts.py`** — `_synthesize_sarvam`/`_async_sarvam`, `_sarvam_request`, `_combine_wavs`; dispatch entries
- **`core/llm_config/schema.py`** — `sarvam` provider type
- **`config/models.yaml`** — `sarvam` + `cartesia` providers active; `sarvam-bulbul`, `cartesia-sonic` model aliases; commented speaker examples
- **`.env.example`** — `SARVAM_API_KEY`, `CARTESIA_API_KEY`
- **`tests/test_tts_sarvam.py`** (new), **`tests/test_tts_cartesia.py`** (new) — request shapes, chunk+concat, error paths, config wiring

### Feature
Topics v1 — per-source topic bucketing (design: `topics v1.md`). Deterministic pins
from publisher-declared structure (seed domain map + URL section slugs; section beats
domain) + one LLM judge (haiku) with pins as constraints; ID-coded wire format
hard-validated against the taxonomy (32 Tier-1 = 28 IAB minus Pets + 4 custom essay
categories); ≤3 Tier-1 × ≤2 Tier-2, per-tag `src` provenance, `{"tags": []}` valid,
LLM-error → pins or NULL (retry). Never fails an ingest. Full suite 822 passed
(+35 new), known-baseline fails unchanged.
- **`core/taxonomy/buckets.py`** (new) — TAXONOMY with stable numeric IDs + custom flags, SEED_DOMAIN_PINS, SECTION_SLUGS, import-time label validation
- **`core/taxonomy/classify.py`** (new) — `resolve_pins`, DSPy `JudgeTopics` (prompt-file `classify_topics`), wire parser/validator, merge, `classify_source()`
- **`core/taxonomy/__init__.py`** (new) — public surface
- **`core/ingest.py`** — `process_source` step 2b: learned-pin lookup (`domain_pins`), classify via executor, write `source.topics`; `source_ingested` event gains `topics_tier1`/`topics_src`
- **`config/models.yaml`** — `classify.topics: haiku-4-5` task binding
- **`api/routes/eval.py`** — `/eval/sources/{id}` returns `topics`; header chip shows Tier-1s (was `metadata.category`)
- **`tests/test_topics_classify.py`** (new) — 35 mock-only tests: taxonomy integrity, pin resolution, wire validation, merge, terminal states

### Refactor
Deleted the 12-label `category` field from the metadata transformation — `topics` is
now the only topical axis; `metadata` keeps only formal facts. Zero functional
consumers existed (grep-verified; only the eval-UI chip, updated above). Old stored
metadata JSON rows keep the key harmlessly.
- **`core/prompts/transformations.py`** — `ExtractMetadata` docstring + output desc drop `category`; module docstring updated
- **`prompts/extract_metadata.txt`** — runtime prompt override rewritten without `category`

### Migration
- **`alembic/versions/0031_source_topics.py`** — `source.topics JSONB` + GIN index; `domain_pins` table (learned pins, Phase 3). Run `python -m alembic upgrade head` when Postgres is up (was down at implementation time).

### Docs
- **`topics v1.md`** — design doc for source topic-bucketing (renamed from the
  earlier `iab tagging v1.md` draft after design review). Final architecture:
  deterministic pins (domain + URL-section, publisher-declared structure only) +
  single LLM judge with pins as constraints; keyword/token matching, LLM hints, and
  the source-level confidence gate were all cut in review. Per-tag `src` provenance
  in `source.topics` JSONB; `domain_pins` table with learned-pin promotion loop;
  4 custom Tier-1s; old 12-label `metadata.category` to be deleted; eval gates
  downstream use. Design only — no code yet; implementation file map inside.

## 2026-07-13 · Arihant + Claude (claude-opus-4-8)

### Test
New unit coverage for the v2.9 features (YouTube source, PostHog analytics, og_image
contract) — 30 tests, all mock-only (no DB / network / API keys; `posthog`,
`youtube-transcript-api`, `yt-dlp` need not be installed). Full suite: 787 passed,
6 known-baseline fails unchanged, 5 e2e errors are pre-existing environmental
(they skip cleanly in isolation).
- **`tests/test_youtube_scraper.py`** — `extract_video_id` patterns; `scrape_youtube`
  5-tuple contract, extra_data merge, title→video_id fallback, empty-transcript and
  unparseable-URL `PermanentError` (mocks `_fetch_transcript_sync`/`_fetch_metadata_sync`)
- **`tests/test_analytics.py`** — `_get_client` no-op when token unset + caching;
  `resolve_distinct_id` firebase_uid-vs-user_id fallback; `capture`/`track` arg
  forwarding and never-raises (mocks `_get_client`/`db_fetchrow`)
- **`tests/test_og_image_contract.py`** — `scrape()` 5-tuple + og_image propagation on
  trafilatura and firecrawl paths; YouTube routing to `scrape_youtube`; validator allows
  YouTube; `SourceSummary.og_image` field

### Chore
Reconciled the diverged v2.9 line: local `v2.9` moved to `origin/v2.9` (og_image,
YouTube, PostHog) and the Apple name/email COALESCE fix (`644b8e8`) cherry-picked on
top so the v2.9 line retains it. CHANGELOG conflict resolved keeping both histories.

## 2026-07-01 · Aditya + Claude (claude-sonnet-5)

### Feature
Backend PostHog analytics — `episode_generated`/`episode_generation_failed` and
`source_ingested`/`source_ingest_failed` events, closing the loop with the
already-instrumented frontend. `distinct_id` is resolved to `firebase_uid` via
a `users` table lookup per event (falls back to internal `user_id` for legacy
api_token-only accounts with no `firebase_uid` on file). No-ops entirely when
`POSTHOG_PROJECT_TOKEN` is unset — safe in local dev without configuring PostHog.
- **`core/analytics.py`** (new) — singleton PostHog client, `resolve_distinct_id()`, `track()`/`capture()`, all fire-and-forget (never raises)
- **`core/ingest.py`** — `process_source` fires `source_ingested` on success, `source_ingest_failed` on final-attempt failure
- **`studio/generator.py`** — `process_episode` fires `episode_generated` on success, `episode_generation_failed` on failure
- **`pyproject.toml`** — added `posthog` dependency
- **`.env.example`** — documented `POSTHOG_PROJECT_TOKEN`, `POSTHOG_HOST`

### Feature
Support YouTube videos as a source type — transcript is fetched via
`youtube-transcript-api` (no API key) and metadata (title/author/thumbnail/
duration/upload_date/view_count) via `yt-dlp`. The scrape contract grows
from a 4-tuple to a 5-tuple `(content, title, author, og_image, extra_data)`;
`extra_data` merges into `source.data` JSONB and is `{}` for non-YouTube paths.
- **`alembic/versions/0030_source_type.py`** — adds `source.source_type` column, defaults existing rows to `'article'`
- **`core/scraper/validator.py`** — YouTube removed from the `_VIDEO` block-list; added `_YOUTUBE` regex + `is_youtube_url()`
- **`core/scraper/youtube.py`** (new) — `scrape_youtube()`: transcript via `youtube-transcript-api`, metadata via `yt-dlp`; optional Tor SOCKS proxy via `YOUTUBE_USE_TOR`
- **`core/scraper/cascade.py`** — `scrape()` branches to `scrape_youtube()` on YouTube URLs; all return paths now 5-tuples
- **`core/ingest.py`** — `process_source` sets `source_type` and merges scraper `extra_data` into `source.data` via `data || $extra_data::jsonb`
- **`pyproject.toml`** — added `youtube-transcript-api`, `yt-dlp` dependencies
- **`.env.example`** — documented `YOUTUBE_USE_TOR`
- **`tests/test_validator.py`**, **`tests/test_url_validator.py`** — updated YouTube rejection tests to reflect new allow behavior; updated tuple-unpacking in cascade tests for the 5-tuple contract

## 2026-07-01 · Aditya + Claude (claude-opus-4-8)

### Test
Update scraper tests to the 4-tuple `(content, title, author, og_image)` contract
introduced by the og_image feature; prod was correct, tests were stale.
- **`tests/test_cascade_scraper.py`** — `_scrape_firecrawl`/`_try_firecrawl_or_fail` mocks → 4-tuples
- **`tests/test_scraper_internals.py`** — firecrawl unpacking + `_scrape_trafilatura`/`_try_firecrawl_or_fail` mocks → 4-tuples
- **`tests/test_url_validator.py`** — `TestCascadingScraper` traf/fire mocks + `scrape()` result unpack → 4-tuples

---

## 2026-06-29 · Aditya + Claude (claude-sonnet-4-6)

### Feature
Scrape and store `og:image` from articles so frontend can display real article thumbnails.
- **`alembic/versions/0029_source_og_image.py`** — migration adding `og_image TEXT` column to `source` table
- **`core/scraper/cascade.py`** — all scrape functions now return 4-tuple `(content, title, author, og_image)`; trafilatura path reads `meta.image`, firecrawl path reads `metadata.ogImage`
- **`core/ingest.py`** — `scrape_url` return type updated to 4-tuple; `og_image` stored in UPDATE alongside title/author
- **`api/schemas.py`** — `SourceSummary` gains `og_image: Optional[str]`
- **`api/routes/sources.py`** — both list-sources SELECTs now include `s.og_image`

---

## 2026-06-20 · Arihant + Claude (claude-opus-4-8)

### Bug Fix
Sign in with Apple — the user's name was never persisted. Root cause (found by auditing the whole
flow incl. the frontend + Apple/Firebase docs): Apple returns name/email to the client **only on the
first authorization** and **never in the identity token**; Firebase does not copy them onto the user,
so the client must capture `credential.fullName` and forward it. The app discarded it, so the backend
read a null `name` from the token and stored null. Fix forwards the captured name/email to the backend
and persists them fill-only.
- **`api/routes/auth.py`** — `AppleLinkRequest` gains optional `name` + `email`; `link_apple` persists
  them with `COALESCE(NULLIF(...), col)` (fill-only, never nulls a stored value) **independent of** the
  refresh-token exchange, so a slow/failed Apple exchange can't lose the name.
- **`api/auth.py`** — provisioning `ON CONFLICT (firebase_uid)` now `COALESCE`s email/name instead of
  overwriting with `EXCLUDED.*`, so a later sign-in that omits them (Apple's first-auth-only behaviour)
  can't wipe stored values. Also makes the no-email / Hide-My-Email path safe (email column is nullable;
  relay addresses are treated as normal emails; returning users resolve by `firebase_uid`).
- **`tests/test_apple_auth.py`** — 9 mock-only tests (no DB/network/deploy): no-email provisioning,
  relay email, re-link by email, ON CONFLICT COALESCE, and `link_apple` name/email fill-only + refresh
  token + independence from exchange failure.
- Frontend (curia-frontend): `context/AuthContext.tsx` captures `credential.fullName`/`email`,
  `updateProfile({displayName})`, forwards them via `linkApple`, and re-fetches the profile after; the
  delete→revoke→re-signin loop then yields a fresh first-auth so Apple resends the name.
- Known follow-up: the client nonce still uses `Math.random` (should move to `expo-crypto`'s secure RNG —
  needs the dep + a native rebuild, deferred).

---

## 2026-06-19 · Arihant + Claude (claude-opus-4-8)

### Test
Integration-test coverage for v2.8's new surface + a stale-doc fix. Verified live against the
deployed v2.8 (bad-format → 422, `author` present in the source list).
- **`tests/test_aws_smoke.py`** — `test_bad_format_override_is_rejected` (valid URL + unknown
  `format` → 422 at the API edge, no row created → free) and `test_source_schema_exposes_author`
  (deployed `/sources` response carries the `author` key). Both in the cheap `TestApiSurface` layer.
- **`tests/test_e2e_generate.py`** — docstring no longer claims `HUME_API_KEY`; documents the real
  TTS options (edge-tts / SMALLEST / DEEPGRAM / ELEVENLABS per `config/models.yaml`). No code change —
  the generation e2e already matches current code (kenji+emeka pairs, current signatures).

## 2026-06-19 · Arihant + Claude (claude-opus-4-8)

### Test
Coverage expansion — 24 new unit-test files (~412 tests) on top of the merged `v2.8`. All mock-only
(no DB / network / API keys); full suite now 747 passing, 6 known-baseline fails unchanged.
- **`tests/`** — `test_account_external`, `test_apple`, `test_blob_storage`, `test_briefing`,
  `test_cascade_scraper`, `test_embedder`, `test_embeddings_facade`, `test_firebase_unit`,
  `test_formats`, `test_ingest_normalise`, `test_ingest_pipeline`, `test_kb_extended`,
  `test_llm_builder`, `test_llm_logger`, `test_logging_setup`, `test_merger`, `test_prompts_loader`,
  `test_resolver`, `test_schema_config`, `test_schemas_api`, `test_scraper_internals`,
  `test_splitter`, `test_tts_facade`, `test_validator`
- **`.coveragerc`** — coverage config; **`.gitignore`** — ignore `.coverage` artifact

## 2026-06-19 · Arihant + Claude (claude-opus-4-8)

### Refactor
Merged `v2.7-sqs` into `v2.8` — reconciled the two diverged backend lines. `v2.8` had branched
from the account-deletion commit and added the format/angle override + author-extraction feature,
but was missing all of `v2.7-sqs`'s later fixes (auth re-link, Apple revoke, Deepgram). After the
merge `v2.8` carries both. Only `CHANGELOG.md` conflicted (content union, date-ordered); the code
files (`api/routes/sources.py`, `worker/handlers/ingest.py`) auto-merged cleanly with both sides'
logic intact (enqueue keeps `format`/`angle` **and** `lane="interactive"`; ingest keeps the
`already_ingested` idempotency guard **and** the `format`/`angle` → `_run_standalone` wiring).
- **`tests/test_url_validator.py`** — fixed the two `TestCascadingScraper` cases that broke when
  `cascade.scrape()` started returning a 3-tuple `(content, title, author)`: mocks now return
  3-tuples and the call sites unpack three values. (Pre-existing breakage on `v2.8`, not the merge.)
- Note: `0028_source_author` is correctly numbered for this branch (single alembic head); RDS still
  needs an idempotent `ALTER TABLE source ADD COLUMN IF NOT EXISTS author TEXT` at deploy time.

## 2026-06-19 · Arihant + Claude (claude-fable-5)

### Bug Fix
- **`api/auth.py`** — Firebase user auto-provisioning crashed with `UniqueViolationError: users_email_key` when a returning person signed in with a NEW `firebase_uid` but an existing email (happens after account deletion or Apple/Google de-authorization — Firebase mints a fresh uid). The `INSERT … ON CONFLICT (firebase_uid)` ignored the separate UNIQUE(email) constraint; the error was swallowed → 401 → "ghost user" on re-signin (affected Google too, not just Apple). Now: on a new uid, look up by email and **re-link** the existing row to the new uid instead of inserting a duplicate. Diagnosed from live `[authdbg]` API logs.

### Bug Fix
- **`core/llm_config/adapters/tts.py`** — Deepgram `/v1/speak` caps input at 2000 chars/request; the generator's ~4500-char segments hit `413 Payload Too Large`. Added sentence-boundary chunking (≤1800 chars) that concatenates the MP3 segments, in both sync + async paths. Found by generating a real Deepgram show end-to-end (9.3-min episode produced + uploaded to S3 + served via presigned playback).
- **`tests/test_tts_deepgram.py`** — chunking test (long text → multiple ≤1800-char calls, segments concatenated)

## 2026-06-18 · Aditya + Claude (claude-sonnet-4-6)

### Feature
Author/publication extraction during source scrape.
- **`core/scraper/cascade.py`** — `_scrape_trafilatura` now reads `meta.author` (falls back to `meta.sitename`); `_scrape_firecrawl` reads `metadata.author` (falls back to `ogSiteName`). Both return a 3-tuple `(content, title, author)`.
- **`core/ingest.py`** — `scrape_url` signature updated to 3-tuple; `author` saved to DB alongside `title`.
- **`api/schemas.py`** — `author: Optional[str]` added to `SourceSummary`.
- **`api/routes/sources.py`** — `s.author` included in both list queries.
- **`alembic/versions/0028_source_author.py`** — migration adding `author TEXT` column to `source`.

### Test
- **`tests/test_schemas.py`** — all 19 existing tests pass; `author` field defaults to `None` on `SourceSummary`.

## 2026-06-18 · Aditya + Claude (claude-opus-4-8)

### Feature
Per-source `format` + `angle` overrides on `POST /sources` (standalone path). The override
plumbing already existed end-to-end in `_run_standalone`/`_create_episode_and_enqueue` but was
hardcoded to `None` at the call site — this wires the API request through the ingest job payload
so the share/add "customize" sheet can pick a show format and supply an editorial angle. Format
accepts frontend slugs (slow-burn/sharp-take/live-wire/open-verdict) or backend names; resolved
via `resolve_format_name` so unknown values 422 at the API edge. Both fields ignored for cluster
sources (standalone=False).
- **`api/schemas.py`** — `CreateSourceRequest` gains `format` (validated/normalised) + `angle`
- **`api/routes/sources.py`** — `create_source` threads `format`/`angle` into the ingest enqueue payload + log line
- **`worker/handlers/ingest.py`** — `handle_ingest` reads `format`/`angle` from payload, passes as `show_name`/`angle_override` to `_run_standalone` (was `None`)

## 2026-06-17 · Arihant + Claude (claude-fable-5)

### Feature
Deepgram Aura TTS provider — available like every other TTS provider (config-selectable per speaker). Plain REST (`POST /v1/speak?model=<voice>&encoding=mp3`, `Authorization: Token`), returns MP3; the Deepgram "voice" is the model query param, so a speaker's `voice_id` (e.g. `aura-2-thalia-en`) overrides the alias `model_id`. Native sync + async paths, stub fallback when `DEEPGRAM_API_KEY` unset — same contract as the others.
- **`core/llm_config/schema.py`** — `deepgram` added to `ProviderType`
- **`core/llm_config/adapters/tts.py`** — `_synthesize_deepgram` + `_async_deepgram`; dispatch + `output_format` (mp3) wired
- **`config/models.yaml`** — `deepgram` provider + `deepgram-aura` model alias (model_id `aura-2-thalia-en`, bit_rate 48000)
- **`.env.example`** — `DEEPGRAM_API_KEY`
- **`tests/test_tts_deepgram.py`** — 6 tests: config wiring, mp3 output, sync/async request shape, voice_id→model override, error handling (httpx mocked)

### Feature
Sign in with Apple — token revocation on account deletion (App Store 5.1.1(v)). Required because Apple only returns name/email on first authorization and won't resend until the app is de-authorized; deleting without revoking left a nameless "ghost" account on re-signin. Revoke also satisfies Apple's review requirement.
- **`core/apple.py`** — new; ES256 client-secret JWT (PyJWT + .p8), `exchange_code` (auth code → refresh token, tries dev+prod client_ids), `revoke`. Best-effort, never raises.
- **`api/routes/auth.py`** — `POST /auth/apple` stores the refresh token + client_id per user at sign-in
- **`core/account.py`** — `delete_account` revokes the Apple grant (outside the txn, best-effort) before wiping the user
- **`pyproject.toml`** — `pyjwt[crypto]>=2.8`
- **`tests/test_account_delete.py`** — mock updated for the new apple-token lookup
- Requires: `users.apple_refresh_token` + `users.apple_client_id` columns (idempotent ALTER on RDS) and APPLE_* secrets (Team ID, Key ID, .p8, client_ids)

## 2026-06-11 · Arihant + Claude (claude-fable-5)

### Feature
Account deletion — App Store Guideline 5.1.1(v): in-app account delete that wipes ALL associated data (not deactivation). Drift-proof: discovers every `user_id` table (and source children by `source_id`, plus the `source_similarity` source_a/source_b oddball) from information_schema at runtime and wipes them in FK-safe order in one transaction (source-children → user_id tables → source → users last), then best-effort cleans S3 audio + the Firebase auth user outside the txn so their failure can't roll back the DB delete. Needed because most user tables key on `user_id` as plain TEXT (no FK/cascade, per 0002).
- **`core/account.py`** — new; `delete_account(user_id)` returns a per-table delete summary
- **`api/routes/me.py`** — `DELETE /me` (204, idempotent) gated by `current_user`
- **`core/storage/blob.py`** — `delete_blobs(keys)` (s3 batched + local), best-effort/idempotent
- **`core/firebase.py`** — `delete_user(uid)` best-effort (UserNotFound = success; never raises)
- **`tests/test_account_delete.py`** — 7 tests: full table coverage, users-deleted-last, source-child + similarity sweep, no-sources skip, protected tables, external cleanup called + failure-isolated + skipped when no uid

### Feature
SQS queue backend (Phase 4) — switchable transport behind the existing queue interface. `CURIA_QUEUE_BACKEND=postgres` (default, unchanged) or `sqs` (two lane queues: `curia-interactive` for user-watched jobs, `curia-background` for pipeline-chained work). Jobs table becomes a write-only audit record under sqs (powers /jobs polling + dedup). Retries via visibility expiry (900s > 600s handler cap, no heartbeat); 3 receives → DLQ; PermanentError deletes immediately.
- **`core/queue.py`** — `lane` param + `_DEFAULT_LANE`; backend toggle; SQS send on enqueue (audit row first, `fail_permanently` if publish fails); new `sqs_receive/sqs_ack/sqs_fail/sqs_fail_permanently`; `reap_stale` documented postgres-only
- **`worker/main.py`** — handler execution extracted to transport-agnostic `_execute()`; new `_process_one_sqs()` (lane-pinned via `CURIA_WORKER_LANE`, long-poll, delete-on-ack, leave-on-fail); main loop branches on backend; reap + empty-sleep gated to postgres
- **`worker/handlers/ingest.py`**, **`worker/handlers/generate_episode.py`** — idempotency guards: skip (ack) when `source/episode.status='ready'` — makes SQS at-least-once redelivery a no-op
- **`api/routes/sources.py`**, **`api/routes/ideas.py`**, **`api/routes/episodes.py`** — explicit `lane="interactive"` on user-triggered enqueues
- **`tests/test_queue_sqs.py`** — 19 new tests (lane routing, send-failure handling, receive/ack/fail semantics, worker dispatch outcomes) with mocked boto3
- **`tests/test_worker_reliability.py`** — timeout-wrapper test follows the `_execute` refactor and now asserts both transport paths route through it

### Test
- **`scripts/stress_test.py`** — stress harness: `reads` (read-path load, latency percentiles), `failburst` (queue-churn via bot-walled URLs, near-free), `genburst` (full-pipeline burst, costed + confirmed), `watch` (live queue/task/status dashboard). First runs: 1 API task ≈ 230 req/s sweet spot at 50 concurrent, graceful queuing (0 errors) but p99 11s at 100 concurrent → API autoscaling added to Phase 5 list; failburst ×10 all terminal in <40s, DLQ clean.

### Test
- **`tests/test_aws_smoke.py`** — live verification suite for the deployed stack, runnable anytime (`CURIA_SMOKE=1`, optional `CURIA_SMOKE_FULL=1`). Four layers: infra/config (services at desired counts, running image digests == ECR :latest, queue visibility/redrive, DLQ depth as alert, backlog sanity, task-def env drift, S3 public-block, secrets), API surface (health, auth edges, 404/422, presigned audio serves real MP3 bytes), full pipeline happy path (save→ingest→chained episode→ready→S3 object, API idempotency), failure invariant (permanently failing URLs must reach a terminal state — regression for the perpetual-Queued bug). Token self-served from Secrets Manager. Caught a stale curia-api image on its first run.
- **`infra/RUNBOOK.md`** — smoke-suite usage section

### Bug Fix
- **`worker/handlers/ingest.py`** — sources now marked `failed` on `PermanentError` at any attempt (previously only on the final attempt): a 403/404 on attempt 1 is never retried under SQS (`fail_permanently` deletes the message), so the source stayed `scraping` forever and the app showed a perpetual Queued card. Found via the Wikipedia-403 e2e run; stuck row healed manually on RDS.

### Bug Fix
- **`worker/handlers/ingest.py`** — idempotency guard made baton-aware: the original "source ready → skip" guard could drop the chained episode if ingest succeeded but the chain-enqueue failed (redelivery would skip and never spawn the episode). Now: ready + episode exists → skip (true duplicate); ready + no episode → skip the re-scrape but resume the chain.
- **`infra/ecs-task-def-api.json`**, **`infra/ecs-task-def-worker-{interactive,background}.json`** — SQS env (backend toggle, queue URLs, worker lane); two lane worker families replace the single worker def

### Bug Fix
- **`core/notifications.py`** — notification dedup was check-then-send (racy across multiple worker tasks: both pass the NOT-EXISTS check, both push). Now claim-first: atomic `INSERT … ON CONFLICT DO NOTHING RETURNING` claims the (user, type, day) before sending; loser skips; claim released on send failure so the day isn't burned

### Config
- **`config/models.yaml`** — speaker TTS bindings reverted hume-octave → smallest-lightning (kenji→james, arjun→george, emeka→emily, recovered from pre-bdf4b42 state): no HUME_API_KEY exists in any environment, so Hume bindings would stub-silence TTS on the AWS deploy; SMALLEST_API_KEY is provisioned. Hume/voice work can resume later by re-adding the key.

### Config
AWS migration phases 0–2 executed (infra in account 617341601034, us-east-1): ECR `curia`, S3 `curia-audio` (private), VPC `curia` + NAT + 4 SGs, RDS `curia-db` (Postgres 18.4 — recreated from 16.11 for parity with Railway prod, pgvector 0.8.2), 7 Secrets Manager entries, prod DB restored (0 errors, all row counts match, alembic at 0028), audio sync R2→S3 started.
- **`infra/RUNBOOK.md`** — registry filled with live resource IDs; CGNAT egress-IP gotcha; Railway env truths (OpenAI embeddings not Voyage; no Hume key; INTERNAL_SECRET/JINA unused by this branch); PG18 + branch-drift findings
- **`infra/ecs-task-def-api.json`**, **`infra/ecs-task-def-worker.json`** — account id filled; secrets corrected to the real set (anthropic, openai, firecrawl, smallest, firebase SA)

## 2026-06-09 · Arihant + Claude (claude-fable-5)

### Config
AWS migration Phase 0 — infra artifacts for the ECS deployment (no app code changes).
- **`infra/ecs-task-def-api.json`** — Fargate task definition template for the API service (uvicorn, port 8000, Secrets Manager-injected env, awslogs, /health healthcheck)
- **`infra/ecs-task-def-worker.json`** — Fargate task definition for the worker (same image, `python -m worker.main`, stopTimeout 120)
- **`infra/RUNBOOK.md`** — phase-by-phase console/CLI runbook (VPC, SGs, RDS+pgvector, Secrets, data migration, ECS/ALB, SQS, cutover); doubles as the resource registry for the later Terraform pass

### Config
Audio storage decision changed: R2 → S3 (`curia-audio`). Task roles replace R2 keys (verified `core/storage/blob.py` falls back to the boto3 default credential chain when no explicit keys are set; `audio_url` stores keys not URLs, so migration is object-copy + env flip — no code change).
- **`infra/ecs-task-def-api.json`** — dropped `CURIA_S3_ENDPOINT` + R2 key secrets; set `CURIA_S3_BUCKET=curia-audio`, `CURIA_S3_REGION=us-east-1`
- **`infra/ecs-task-def-worker.json`** — same storage env changes
- **`infra/RUNBOOK.md`** — added 1e (S3 bucket), 2b (R2→S3 object sync + delta re-sync at cutover), task-role S3 policies + presigned-URL/temp-credential caveat

## 2026-06-07 · Arihant + Claude (claude-opus-4-8)

### Docs
Seeded living project-tracking docs ahead of the coordinated frontend + backend changes and
the Railway→AWS migration. Grounded entirely in a fresh read of both repos (backend `v2.7-final`,
frontend `v2.8` cloned from `github.com/Aurora-Labs-26/curia-frontend`), correcting stale README claims.
- **`CHANGETHOUGHT.md`** — new cross-repo design/decision log: two-repo topology, code-grounded
  backend + frontend summaries, doc-drift corrections (TTS is Hume Octave not edge-tts; S3 storage
  already implemented; mobile app exists), AWS migration readiness map, frontend↔backend contract
  mismatches, open questions, and a decision log we maintain during development.
- **`CHANGELOG.md`** — added pointer to `CHANGETHOUGHT.md` and this seed entry.

---

## 2026-06-09 · Claude (claude-sonnet-4-6)

### Bug Fix
Two scraper fixes for stuck sources.
- **`core/scraper/cascade.py`** — moved `is_twitter` check before `head_check` so X.com links go straight to Firecrawl instead of failing on the 403 HEAD response
- **`worker/handlers/ingest.py`** — on final attempt failure, write `status='failed'` + error back to source row; previously sources stayed in `scraping` indefinitely

---

## 2026-06-08 · Claude (claude-sonnet-4-6)

### Feature
Scheduled push notifications via APScheduler — two daily jobs targeting IST users.
- **`core/notifications.py`** — new file; `send_listen_reminders()` (14:30 UTC) sends batched unplayed-episode nudge; `send_reengagement_reminders()` (05:30 UTC) sends daily for 7 days post-signup to users with zero sources
- **`worker/main.py`** — `_start_scheduler()` starts AsyncIOScheduler with two CronTrigger jobs; scheduler shut down cleanly on SIGTERM
- **`pyproject.toml`** — added `apscheduler>=3.10` dependency

### Migration
- **`alembic/versions/0025_notification_log.py`** — `notification_log` table with UNIQUE(user_id, type, sent_date) to prevent duplicate sends on restarts

---

## 2026-06-07 · Bhabani + Claude (claude-sonnet-4-6)

### Feature
Renamed show display names to match new brand language. These names are used by hosts in spoken intros/outros and in the frontend format picker.
- **`studio/formats.py`** — `DISPLAY_NAMES`: `narrative_drift→Drift`, `clarity_engine→Unpack`, `momentum_loop→Quickie`, `exploration_engine→Explore`
- **`data/mock.ts`** (frontend) — `FORMAT_LABELS`: same mapping applied to frontend slugs

---

## 2026-06-06 · Bhabani + Claude (claude-sonnet-4-6) [third entry]

### Bug Fix
Remixed episodes pulled sources from the user's full archive instead of the original episode's sources, producing extra/wrong sources. Root cause: `GET /episodes` list endpoint didn't include `show_idea_id` in its SELECT, so `EpisodeSummary` returned `null` for that field. The remix screen reads `show.showIdeaId` to lock the new episode to the same sources — but since it was null, the backend had no `show_idea_id`, fell through to `select_episode_sources`, and picked whatever was relevant in the archive.
- **`api/routes/episodes.py`** — added `show_idea_id` to both `GET /episodes` SELECT queries (status-filtered and unfiltered).
- **`api/schemas.py`** — added `show_idea_id: Optional[UUID] = None` to `EpisodeSummary` so the field passes through the response model instead of being silently dropped.

---

## 2026-06-06 · Bhabani + Claude (claude-sonnet-4-6) [second entry]

### Bug Fix
Duplicate `line_index` in `tts_timings` when a single-speaker transcript exceeded the TTS chunk size. When all transcript lines belong to one speaker (e.g. `momentum_loop`), `merge_paragraphs` produces one large paragraph. If its text exceeds `max_chars=4500`, `_split_at_sentences` cuts it mid-line — causing `_assign_line_metadata_to_chunk` to assign the straddling line to both chunks, producing two `tts_timings` entries with the same `line_index`.
- **`studio/generator.py`** — after building `tts_timings`, deduplicate by `line_index` keeping the entry with the largest duration (i.e. the chunk that contains the majority of the line). This is done before the final sort. The `daa2ee9e` episode (momentum_loop) had line_index 20 duplicated; this fix corrects that class of issue for all future generations.

---

## 2026-06-06 · Bhabani + Claude (claude-sonnet-4-6)

### Feature
Smallest.ai is now the default TTS provider. Hume is kept as a commented-out fallback for when word-level timestamps are needed.
- **`config/models.yaml`** — switched default `speaker` bindings from `hume-octave` to `smallest-lightning` for all three hosts: `kenji→william` (Canadian, composed), `arjun→zorin` (American, friendly/powerful), `emeka→julia` (British, dignified). Hume config preserved as commented block for future use.

### Chore
- Changed arjun's Smallest.ai voice from `alec` to `zorin` for better acoustic distinction from kenji.

---

## 2026-05-28 · Arihant + Claude (claude-opus-4-6)

### Bug Fix
### Test
39 new tests for previously untested modules — storage blob, audio streaming, TTS file I/O, eval verdicts.
- **`tests/test_storage_blob.py`** — 8 tests: upload_blob writes/overwrites, upload_file, nested dirs, empty content, concurrent non-blocking
- **`tests/test_audio_stream.py`** — 7 tests: yields per segment, saves WAV, empty transcript, empty TTS response, metadata, non-WAV passthrough
- **`tests/test_tts_file_io.py`** — 12 tests: _write_bytes/_write_wav_from_pcm helpers, synthesize_bytes returns WAV + cleanup + concurrency, synthesize_async stub for all providers, non-blocking
- **`tests/test_eval_verdicts.py`** — 12 tests: append/load JSONL, corrupted lines, stats aggregation, CSV export, empty states

Async correctness pass 4 — last remaining issue.
- **`scripts/synthesize_episode.py`** — `os.makedirs()`, `os.path.exists()`, `synthesize_line()` (subprocess.run), and `stitch_wavs()` (subprocess.run) wrapped in `asyncio.to_thread()`

Async correctness pass 3 — TTS adapters and audio streaming had sync file I/O blocking the event loop.
- **`core/llm_config/adapters/tts.py`** — all 7 async TTS providers (`_async_elevenlabs`, `_async_openai_tts`, `_async_cartesia`, `_async_smallest`, `_async_smallest_with_timings`, `_async_google`, `_async_edge_tts`, `_async_xai`) had sync `open()`, `wave.open()`, `shutil.move()`, `os.unlink()`, `tempfile` writes, or `AudioSegment.from_wav()` calls. All wrapped in `asyncio.to_thread()`. Added `_write_bytes()` helper. `synthesize_bytes()` file read + cleanup also wrapped.
- **`core/audio/stream.py`** — `wave.open()` read/write in async generator wrapped in `asyncio.to_thread()`
- **`core/audio/stream_manager.py`** — same WAV read/write blocking ops wrapped in `asyncio.to_thread()`
- **`api/routes/stream.py`** — `EPISODES_DIR.mkdir()` in WebSocket handler wrapped in `asyncio.to_thread()`

Async correctness pass 2 — more blocking I/O and deprecated APIs found in second audit.
- **`core/storage/blob.py`** — `Path.read_bytes()`, `Path.stat()`, `Path.mkdir()`, `Path.write_bytes()` in async upload functions wrapped in `asyncio.to_thread()`
- **`api/routes/episodes.py`** — `Path.exists()` and `Path.stat()` in audio streaming endpoint wrapped in `asyncio.to_thread()`
- **`optimization/runner/gepa_runner.py`** — `save_prompt()` sync file write wrapped in `asyncio.to_thread()`
- **`core/ingest.py`**, **`studio/generator.py`**, **`scripts/rerun_transformations.py`**, **`worker/main.py`** — `asyncio.get_event_loop()` → `asyncio.get_running_loop()` (deprecated in Python 3.10+)

Async correctness pass 1 — sync blocking calls in async handlers stalled the event loop.
- **`intelligence/idea_generator.py`** — DSPy `evaluate_ideas_batch` and `evaluate_single_idea` calls wrapped in `asyncio.to_thread()` so LLM inference doesn't block the event loop
- **`api/routes/eval.py`** — `_append_verdict()` and `_load_verdicts()` file I/O wrapped in `asyncio.to_thread()`
- **`api/routes/admin.py`** — replaced sync `get_guidelines(task)` with `await get_guidelines_async(task)`
- **`api/routes/me.py`** — same: sync `get_guidelines()` → async `get_guidelines_async()`
- **`studio/generator.py`** — `pydub.AudioSegment.from_mp3()` wrapped in `asyncio.to_thread()`

- **`core/ingest.py`** — `embed_primitive()` only used `core_tensions` + `counterpoints` for clustering embeddings; technical/factual content (docs, guides) that don't produce those insight types were silently skipped and never clustered. Now falls back to all available insights (summary, key_insights, examples, etc.) when primary primitives are empty.

### Test
- **`tests/test_embed_primitive.py`** — 6 tests for `embed_primitive()` fallback logic: uses primary when available, falls back to all insights, skips on empty/null content, handles None embeddings

---

## 2026-05-26 · Arihant + Claude (claude-opus-4-6)

### Bug Fix
Worker reliability — sync DSPy/TTS calls blocked the event loop, preventing SIGTERM handling during Railway deploys. Stale job reaper existed but was never called; its SQL also returned 0 always.
- **`studio/generator.py`** — wrapped `generate_outline()`, `generate_transcript()`, and `synthesize_and_stitch()` in `run_in_executor` so they no longer block the async event loop
- **`core/queue.py`** — fixed `reap_stale()`: changed `fetchrow` with broken RETURNING clause to `fetch` + `RETURNING id`, now correctly counts and logs reaped jobs
- **`worker/main.py`** — added 10-minute handler timeout via `asyncio.wait_for`; added `_maybe_reap_stale()` called every 5 minutes to recover stuck jobs; imported `reap_stale` from queue

### Bug Fix
Episode length stuck at ~7 min regardless of format — transcript prompt had no length calibration. `target_length_minutes` was in the briefing packet but neither prompt converted it to a line count.
- **`studio/formats.py`** — added `LINES_PER_MINUTE` constant (4.3) and `target_lines`/`target_lines_per_segment` properties to `FormatConfig`; included both in `format_config_to_dict`
- **`studio/briefing_builder.py`** — `episode_constraints` now includes `target_lines` and `target_lines_per_segment`, dynamically recalculated when length is overridden
- **`prompts/transcript.txt`** — added LENGTH section as hard constraint (#1 priority): produce exactly `target_lines` entries (±10%), distribute evenly across segments
- **`core/prompts/transcript.py`** — updated fallback docstring to match prompt file
- **`prompts/outline.txt`** — added `target_lines_per_segment` to format_config instructions so outline allocates enough material per segment
- **`core/prompts/outline.py`** — updated fallback docstring to match prompt file
- **`optimization/guidelines/transcript.py`** — replaced "6 to 80 lines" with format-driven `target_lines` constraint

---

## 2026-05-25 · Bhabani + Claude (claude-sonnet-4-6)

### Feature
- **`prompts/transcript.txt`** — added mandatory INTRO and OUTRO blocks to the transcript prompt. The intro (3–5 lines) warms the listener in before any content, references their pile, and builds anticipation. The outro (3–4 lines) closes the show warmly, includes the compounding-value line (more you save/listen, better it gets), and signs off genuinely.
- **`studio/generator.py`** — `_format_listener_hints()` now injects the listener's first name (from `kb.identity.name`) into the speaker definition so the host uses it once in the intro greeting. No-ops silently if name is not set. Removed the old "Never introduce the show" constraint which was preventing any host presence at the open.

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
