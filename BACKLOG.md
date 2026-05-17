# Curia v2 — Backlog

Last updated: 2026-05-18

Design docs:
- Auto-generation pipeline: `AUTO_GENERATION_DESIGN.md`
- Share sheet, remix sheet, clustering cache: `SHARE_REMIX_DESIGN.md`

---

## 1. Auto-Generation Pipeline

Make the pipeline fully automatic — no manual triggers. When a bookmark is added, episodes appear in the feed without any user action.

**Design:** `AUTO_GENERATION_DESIGN.md`

### Backend

- [x] **AUTO-BE-1** — `worker/handlers/ingest.py`: after source reaches `ready`, enqueue `generate_ideas` only if no `generate_ideas` job is already queued/running for this user (coalesce)
- [x] **AUTO-BE-2** — `intelligence/idea_generator.py`: add `diff_clusters` node between `cluster_sources` and `evaluate_ideas` — filters out clusters whose source_id set already exists in `show_idea`
- [x] **AUTO-BE-3** — `intelligence/idea_generator.py`: remove `DELETE FROM show_idea WHERE generated = false` from `save_ideas` — append only, deduplication handled by diff_clusters
- [x] **AUTO-BE-4** — `intelligence/idea_generator.py`: add format validation in `save_ideas` — if LLM returns unknown format string, fall back to `clarity_engine`
- [x] **AUTO-BE-5** — `intelligence/idea_generator.py`: add `auto_generate` node after `save_ideas` — creates episode rows and enqueues `generate_episode` jobs for each new idea written
- [x] **AUTO-BE-6** — `intelligence/idea_generator.py`: update `IdeaGenState` to include `auto_generated_count: int`
- [x] **AUTO-BE-7** — `worker/handlers/__init__.py`: wire updated graph (new nodes visible to worker)

### Frontend

- [ ] **AUTO-FE-1** — `app/(tabs)/studio.tsx`: polling already implemented (5s when any episode is loading) — verify it handles the new auto-generated episodes appearing without user action
- [ ] **AUTO-FE-2** — `app/(tabs)/studio.tsx`: add empty state for fresh users with zero episodes — CTA to add first bookmark

---

## 2. Share Sheet Integration

Save bookmarks to backend on share. Wire "Queue It" to trigger episode generation with optional config overrides.

**Design:** `SHARE_REMIX_DESIGN.md` — Parts 1, 2, 3

### Backend

- [x] **SHARE-BE-1** — `api/routes/generate_from_source.py`: new `POST /generate-from-source` endpoint — validates source belongs to current user, enqueues `generate_from_source` job, returns `{ job_id }`
- [x] **SHARE-BE-2** — `worker/handlers/generate_from_source.py`: new handler for `standalone: true` path — runs `evaluate_ideas` on single source, creates episode with config overrides
- [x] **SHARE-BE-3** — `worker/handlers/generate_from_source.py`: `standalone: false` path — runs full idea generator pipeline, applies overrides to episodes containing this `source_id`
- [x] **SHARE-BE-4** — `worker/handlers/__init__.py`: register `generate_from_source` handler

### Frontend

- [x] **SHARE-FE-1** — `app/share/index.tsx`: call `api.addSource(url)` on mount, fire-and-forget — show error variant of toast if it fails (duplicate or network error)
- [x] **SHARE-FE-2** — `app/share/customize.tsx`: wire "Queue It" button — call `api.generateFromSource(...)` with mix level and config overrides, then `fadeAndClose()`
- [x] **SHARE-FE-3** — `app/share/customize.tsx`: map mix meter value to `standalone` boolean — `0.0` (Just this) → `true`, `0.5/1.0` (Balanced/Max) → `false`
- [x] **SHARE-FE-4** — `app/share/customize.tsx`: parse config overrides — duration string → `length_minutes` int, host → `speaker` lowercase, format slug → backend `show_name`
- [x] **SHARE-FE-5** — `services/api.ts`: add `addSource(url)` method — `POST /sources`
- [x] **SHARE-FE-6** — `services/api.ts`: add `generateFromSource(params)` method — `POST /generate-from-source`

---

## 3. Remix Sheet Integration

Wire remix screen to real episode data and real episode creation.

**Design:** `SHARE_REMIX_DESIGN.md` — Part 2

### Backend

No new endpoints needed. Uses existing `POST /episodes`.

### Frontend

- [x] **REMIX-FE-1** — `app/remix/[id].tsx`: replace `MOCK_SHOWS` with `api.episode(id)` fetch on mount — pre-populate format, host, duration tiles from real episode
- [x] **REMIX-FE-2** — `app/remix/[id].tsx`: add `FORMAT_TO_BACKEND` mapping (frontend slug → backend show_name) and `snapDuration` helper (minutes int → nearest duration string)
- [x] **REMIX-FE-3** — `app/remix/[id].tsx`: wire "Remix Show" button — call `api.createEpisode(...)` with mapped params, show loading state on button, navigate to `now-playing/[newId]` on success
- [x] **REMIX-FE-4** — `app/remix/[id].tsx`: editorial direction = original episode description + user angle override (appended, not replaced)
- [x] **REMIX-FE-5** — `services/api.ts`: add `createEpisode(params)` method — `POST /episodes`

---

## 4. Clustering Cache

Cache pairwise cosine similarity scores to avoid O(N²) recomputation on every pipeline run.

**Design:** `SHARE_REMIX_DESIGN.md` — Part 5

### Backend

- [x] **CACHE-BE-1** — `alembic/versions/0013_source_similarity.py`: new migration adding `source_similarity` table with `(source_a, source_b, score, created_at)` — primary key on `(source_a, source_b)`, reverse index on `(source_b, source_a)`, `ON DELETE CASCADE`
- [x] **CACHE-BE-2** — `intelligence/idea_generator.py`: batch embedding fetch — single `WHERE source_id = ANY($ids::uuid[])` query replaces per-source loop
- [x] **CACHE-BE-3** — `intelligence/idea_generator.py`: cache-aware pairwise scoring — reads `source_similarity` before compute, bulk-inserts new pairs after the run

---

## 5. Audio Playback

### Backend

- [x] **AUDIO-BE-1** — `api/routes/episodes.py`: replaced `FileResponse` with range-aware `StreamingResponse` — handles `Range: bytes=` header, returns 206 + `Content-Range` / `Accept-Ranges`

### Frontend

- [x] **AUDIO-FE-1** — install `expo-av`
- [x] **AUDIO-FE-2** — `context/PlaybackContext.tsx`: replace fake `setInterval` timer with `Audio.Sound` — load from `GET /episodes/{id}/audio`, wire `onPlaybackStatusUpdate` to progress state
- [x] **AUDIO-FE-3** — `context/PlaybackContext.tsx`: handle audio focus, background playback (`staysActiveInBackground: true`, `playsInSilentModeIOS: true`)
- [x] **AUDIO-FE-4** — `context/PlaybackContext.tsx`: on episode load, seed progress from `show.progress` (already mapped in `mapEpisode`)
- [x] **AUDIO-FE-5** — `services/api.ts`: add `updateProgress(id, progress, listened)` method — `PUT /episodes/{id}/progress`
- [x] **AUDIO-FE-6** — `context/PlaybackContext.tsx`: call `api.updateProgress` every 10s during playback and on pause/complete

---

## 6. Remaining Frontend Integration Gaps

### Profile screen

- [x] **PROFILE-FE-1** — `app/profile/index.tsx`: replaced hardcoded name/email with `user?.name` / `user?.email` from `useAuth()` — populated from `api.me()` at sign-in
- [ ] **PROFILE-FE-2** — `api/routes/`: new `GET /me/stats` endpoint returning shows count and minutes listened (computed from episode table) — or compute on frontend from episodes list

### Player screen

- [ ] **PLAYER-FE-1** — `app/(tabs)/player/[id].tsx`: replace `MOCK_SHOWS` with `api.episode(id)` fetch on mount

### Pile tab

- [ ] **PILE-FE-1** — `app/(tabs)/index.tsx`: build Pile screen — fetch from `api.sources()`, render bookmark list with domain, title, status, savedAgo
- [ ] **PILE-FE-2** — `app/(tabs)/index.tsx`: empty state when no sources — CTA to share first link

---

## 7. Existing Bugs

- [ ] **BUG-01** — failed URL scrape marks source as `ready` instead of `failed`
- [ ] **BUG-03** — `length_minutes` validation (ge=3, le=30) not enforced by FastAPI
- [ ] **BUG-SPEAKER** — unknown speaker name accepted at API layer, only fails in worker

---

## 8. Pipeline Quality

- [ ] **QUALITY-01** — `RE-ROLL`: keep better of two transcript attempts (currently re-roll can ship worse transcript)
- [ ] **QUALITY-02** — add floor rules to transcript generation prompt so judge scores are meaningful
- [ ] **OPTIMIZATION** — GEPA loop never run; needs completed episodes as examples first

---

## 9. Infrastructure (pre-hosting)

- [ ] **INFRA-01** — audio storage: move from local disk (`EPISODES_DIR`) to Cloudflare R2 — `GET /episodes/{id}/audio` returns signed URL instead of `FileResponse`
- [ ] **INFRA-02** — jobs table: add `run_at TIMESTAMPTZ` column + update `dequeue` to respect it — enables batch-with-time-fallback trigger strategy for idea generation
- [ ] **INFRA-03** — decide TTS provider long-term: Smallest.ai working now, evaluate ElevenLabs or local model

---

## 10. Test Plan Fixes

- [ ] Fix `PUT /me/kb` example — wrong wrapper in `TEST_PLAN.md`
- [ ] Fix `reading_volume_per_week` type in `TEST_PLAN.md` (str not int)
- [ ] Fix TC-2.2 polling script — breaks on control chars in JSON
- [ ] Add fresh test user to Setup for TC-11.1
- [ ] Add poll-until-done to TC-11.6 dedup check
