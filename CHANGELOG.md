# Changelog

Each entry: **date · who made the change · what changed and why.**

---

## 2026-05-18 · Claude (claude-sonnet-4-6) — audio range requests + profile real data

### Bug Fix
- **`api/routes/episodes.py`** — replaced `FileResponse` in `GET /episodes/{id}/audio` with a range-aware `StreamingResponse`; parses `Range: bytes=start-end` header, returns 206 with `Content-Range` / `Accept-Ranges` headers for correct audio scrubbing; falls back to full 200 response when no Range header present

### Feature
- **`app/profile/index.tsx`** — replaced hardcoded `"Bhabani Mohapatra"` and `"bhabani@curia.fm"` with `user?.name` and `user?.email` from `useAuth()` — data already populated from `api.me()` on sign-in

---

## 2026-05-18 · Claude (claude-sonnet-4-6) — generate-from-source endpoint + handler

### Feature
New `POST /generate-from-source` endpoint and worker handler for share sheet's "Queue It" action.
- **`api/routes/generate_from_source.py`** — new route: validates source ownership, resolves format slug, enqueues job, returns `{ job_id }`
- **`worker/handlers/generate_from_source.py`** — new handler: `standalone=true` path runs `evaluate_single_idea` on one source, writes `show_idea` (generated=true), creates episode with overrides; `standalone=false` path runs full `run_idea_generator` pipeline then patches any newly-queued episodes containing the source_id with user overrides
- **`worker/handlers/__init__.py`** — registered `generate_from_source` handler
- **`api/main.py`** — wired `generate_from_source.router` into the FastAPI app

---

## 2026-05-17 · Claude (claude-sonnet-4-6) — idea pipeline: dedup, caching, auto-generation

### Feature
Coalescing auto-trigger: after ingest completes, enqueue `generate_ideas` only if no such job is already queued or running for that user.
- **`worker/handlers/ingest.py`** — after `process_source`, fetch `user_id` from `source` table via `db_fetchrow`; check `jobs` for existing pending `generate_ideas`; enqueue if none found

### Feature
`diff_clusters` node filters out clusters whose exact source-id sets already exist in `show_idea`, preventing duplicate ideas across runs.
- **`intelligence/idea_generator.py`** — added `diff_clusters` node between `cluster_sources` and `evaluate_ideas`; added `auto_generated_count: int` to `IdeaGenState`; batched embedding fetch (single `ANY($ids::uuid[])` query replaces per-source loop); similarity score caching in `source_similarity` table (cache-read before compute, cache-write after); removed destructive `DELETE FROM show_idea` in `save_ideas`; added format validation fallback to `clarity_engine` in `save_ideas`; added `auto_generate` node after `save_ideas` — creates `episode` rows and enqueues `generate_episode` jobs for all freshly saved ideas; updated `build_graph()` and `run_idea_generator` initial state

### Migration
- **`alembic/versions/0013_source_similarity.py`** — new `source_similarity` table (`source_a`, `source_b`, `score`, `created_at`; PK on `(source_a, source_b)`; reverse index `source_similarity_b_idx`)

---

## 2026-05-17 · Bhabani + Claude (claude-sonnet-4-6) — Episode display fields + progress

### Feature
Added description, chapters, playback progress to episodes for frontend shows tab.
- **`alembic/versions/0012_episode_display_fields.py`** — migration adding `description TEXT`, `chapters JSONB`, `play_progress FLOAT`, `listened BOOLEAN` to `episode` table
- **`studio/generator.py`** — added `_derive_display_fields(outline, duration_seconds)`: extracts `thread` as `description`, builds `chapters` array `[{id, title, start_minute}]` from outline segments with evenly distributed start times; wired into completion UPDATE
- **`api/schemas.py`** — moved `duration_seconds`, `source_ids` up to `EpisodeSummary`; added `description`, `chapters`, `play_progress`, `listened` to `EpisodeSummary`; `EpisodeDetail` inherits all, keeps `transcript`, `outline`, `audio_path`, quality fields
- **`api/routes/episodes.py`** — updated both list SELECTs and detail SELECT to include all new fields; added `PUT /episodes/{id}/progress` endpoint (body: `{play_progress: 0.0–1.0, listened: bool}`, returns 204)

---

## 2026-05-17 · Bhabani + Claude (claude-sonnet-4-6) — Firebase auth integration

### Feature
Firebase Auth wired between curia-frontend and curia-v2 backend.

**Backend (`curia-v2`):**
- **`.env`** — added `GOOGLE_APPLICATION_CREDENTIALS=~/.secrets/curia-firebase-adminsdk.json`
- `firebase-admin` installed via pip; `core/firebase.py` + `api/auth.py` already built for this — no code changes needed
- Verified: `init_firebase()` succeeds with service account

**Frontend (`curia-frontend`):**
- Installed `@react-native-firebase/app`, `@react-native-firebase/auth`, `@react-native-google-signin/google-signin`
- **`app.config.ts`** — added `@react-native-firebase/app`, `@react-native-firebase/auth`, `@react-native-google-signin/google-signin` plugins; added `googleServicesFile` to iOS + Android config; added `REVERSED_CLIENT_ID` as iOS URL scheme
- **`services/api.ts`** — replaced mock JWT store with `auth().currentUser.getIdToken()` (Firebase ID token); `request()` sends it as `Authorization: Bearer`; `api.me()` hits real `GET /auth/me`
- **`context/AuthContext.tsx`** — replaced mock sign-in with real `GoogleSignin.signIn()` → `auth().signInWithCredential()`; `onAuthStateChanged` drives user state; `api.me()` provisions backend user row on first sign-in
- **`app/auth/index.tsx`** — wired "Continue with Google" button to `signInWithGoogle()`
- **`.env.development`** — filled in real client IDs from Firebase credential files

**Credentials placed:**
- `~/.secrets/curia-firebase-adminsdk.json` — service account (backend)
- `ios/GoogleService-Info.plist` — iOS Firebase config
- `android/app/google-services.json` — Android Firebase config

---

## 2026-05-17 · Bhabani + Claude (claude-sonnet-4-6)

### Feature
Store actual audio duration on episode for frontend progress bar.
- **`alembic/versions/0011_episode_duration_seconds.py`** — migration adding `duration_seconds INTEGER` (nullable) to `episode` table
- **`studio/generator.py`** — `synthesize_and_stitch()` and `synthesize_and_stitch_v2()` now return `int` (actual rendered seconds via `len(body) // 1000`); `process_episode` captures the return value and passes it as `$duration_seconds` in the UPDATE
- **`api/schemas.py`** — `EpisodeDetail` gains `duration_seconds: Optional[int]`
- **`api/routes/episodes.py`** — `GET /episodes/{id}` SELECT now includes `duration_seconds`

### Feature
Format name matching between frontend and backend (on `v2-hardening` branch).
- **`studio/formats.py`** — added `frontend_name` (e.g. `"slow-burn"`) and `display_name` (e.g. `"Slow Burn"`) fields to `FormatConfig`; added secondary index `_FRONTEND_TO_BACKEND`; added `resolve_format_name()` which accepts either the backend canonical name or the frontend slug
- **`api/schemas.py`** — added `FormatEntry` response model; added `format_frontend_name` and `format_display_name` fields to `EpisodeSummary`
- **`api/routes/episodes.py`** — added `GET /formats` endpoint (no auth required); `POST /episodes` now calls `resolve_format_name()` so frontend can send `"slow-burn"` and backend stores `"narrative_drift"`; list + detail responses enriched with frontend name + display label via `_enrich_row()`
- **`tests/test_mappers_backend.py`** — added 5 new tests: `display_names`, `unique_slugs`, `resolve_backend_names`, `resolve_frontend_slugs`, `get_format_accepts_frontend_slug`; all 11 pass

Mapping:
| Frontend slug | Backend name | Display label |
|---|---|---|
| `slow-burn` | `narrative_drift` | Slow Burn |
| `sharp-take` | `clarity_engine` | Sharp Take |
| `live-wire` | `momentum_loop` | Live Wire |
| `open-verdict` | `exploration_engine` | Open Verdict |

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
