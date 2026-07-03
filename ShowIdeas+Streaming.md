# ShowIdeas + Streaming — Feature Plan

## Overview

Replace the current auto-episode-generation flow with a user-driven "show idea browser". Every URL the user saves runs through ingest + clustering and produces `show_idea` rows. The user browses ideas, previews one (triggering an outline call), and explicitly chooses to generate an episode. Episode generation is always user-initiated, never automatic.

---

## Current Flow (to be replaced)

```
POST /sources → ingest → scrape → transform → embed → standalone LLM → show_idea (generated=true) → episode queued automatically
```

- `standalone=True` path: one article → one episode, always
- `standalone=False` path: batch cluster → show_ideas → auto-generate (also being removed)
- User has no choice — 20 articles = 20 episodes auto-generated

---

## New Flow

### 1. Source Ingest (unchanged pipeline, new endpoint behaviour)

```
POST /sources → ingest → scrape → transform → embed → cluster pipeline → show_idea rows (generated=false)
```

- `standalone` param removed — always run cluster pipeline after every ingest
- Pipeline stops at `show_idea` — no episode is created automatically
- Every new article triggers a full re-cluster of all user sources

### 2. Show Ideas Tab (new frontend tab)

- New tab in the app surfacing all `generated=false` show ideas for the user
- Each **idea card** shows:
  - `angle` as the description
  - Source count + source domain names (from `source_ids`)
  - Format name (Drift / Unpack / Quickie / Explore)
  - `title` and `duration_estimate_seconds` if outline already cached on the idea
- Empty state: no ideas yet (sources still ingesting, or no sources added)

### 3. Idea Detail Page (new frontend screen)

- User taps an idea card → opens Idea Detail page
- If `show_idea.outline` is already populated → render immediately (cached)
- If not → trigger outline LLM call → store result on `show_idea` → render
- Detail page shows:
  - Real `title` (from outline)
  - `description` / thread (one-sentence narrative spine)
  - Duration estimate
  - Chapter list (provisional, evenly distributed)
  - Source list
- **"Generate Episode"** button at the bottom

### 4. Episode Generation (user-initiated)

- User taps "Generate Episode" on the detail page
- Creates episode row, copies outline from `show_idea` across, skips `outlining` stage
- Episode goes straight to `transcribing → script_ready → synthesizing → ready`
- `show_idea.generated` flips to `true` → idea disappears from the ideas tab
- Episode appears in the library/episodes tab

---

## show_idea Schema Changes

Add the following columns to the `show_idea` table:

| Column | Type | Purpose |
|---|---|---|
| `title` | `TEXT` | From outline LLM call |
| `description` | `TEXT` | `outline["thread"]` — one-sentence narrative spine |
| `duration_estimate_seconds` | `INTEGER` | Estimated duration from outline segment count |
| `outline` | `JSONB` | Full outline JSON (title, thread, segments) |
| `chapters` | `JSONB` | Provisional chapters (evenly distributed, no real timestamps yet) |
| `superseded` | `BOOLEAN` | `true` when retired by invalidation rule (default `false`) |

---

## Idea Invalidation Rule

After every cluster run, retire stale ideas so the user always sees a coherent, up-to-date set.

**Rule:** After writing new cluster ideas, for each source that appears in a newly written cluster — invalidate any `generated=false` idea that contains that source but has a **different** `source_ids` set than the new cluster. Mark it `superseded=true`.

**Examples:**
- `[A2]` exists → new cluster `[A2, A4, A6]` written → `[A2]` is superseded (A2 now belongs to a larger cluster)
- `[A2, A4]` exists → new cluster `[A2, A4, A6]` written → `[A2, A4]` is superseded
- `[A2, A4, A6]` exists → new cluster `[A2, A6, A7]` written → `[A2, A4, A6]` is superseded (A2 and A6 now belong to a different cluster)
- `[A1]` exists → `[A2, A4, A6]` written → `[A1]` untouched (no shared sources)

**Key constraint:** Only `generated=false` ideas are ever invalidated. `generated=true` ideas are consumed (have an episode) and are never touched.

**Outline cache on superseded ideas:** When an idea is superseded, its cached outline is discarded with it — no stale outlines floating around.

---

## Outline Caching on show_idea

The outline LLM call result is stored on the `show_idea` row, not on an episode row.

- First tap of an idea card → outline not cached → run LLM call → store on `show_idea` → render detail page
- Subsequent taps (same session or next session) → outline already cached → render immediately, no LLM call
- When "Generate Episode" is tapped → episode row created, outline copied from `show_idea` → episode skips `outlining` stage and goes straight to `transcribing`
- If idea is superseded before user generates → the whole row is retired, cached outline discarded cleanly

---

## Episode Generation Status Flow

### Current
```
queued → outlining → transcribing → script_ready → synthesizing → ready
```

### New (user-initiated, cached outline valid)
```
queued → transcribing → script_ready → synthesizing → ready
```

### New (user-initiated, outline not cached or invalidated by customisation)
```
queued → outlining → transcribing → script_ready → synthesizing → ready
```

The `outlining` stage runs whenever the episode row has no valid outline to copy — either the
outline job hadn't finished when the worker dequeued the episode, or the user customised
format/angle/length (which invalidates the cached outline).

---

## Simultaneous Episode Generation

- Multiple episodes can be queued simultaneously — no blocking
- The worker queue is the natural throttle — jobs are processed FIFO by priority
- Both episodes eventually complete; user sees generating state for each in the episodes tab
- No extra logic needed — existing queue handles it

---

## Episode Cancel / Delete

Cancel is only supported when the episode job is still `queued` (not yet picked up by a worker).

- `DELETE /jobs/:id` — new endpoint; succeeds only if job `status=queued`; marks job `cancelled`, deletes or fails the episode row
- If job is `running` → return 409 "Episode generation already started, cannot cancel"
- Frontend shows cancel button only while episode `status=queued`; hides/disables once it moves to `transcribing`
- No worker changes needed — worker never sees a cancelled job

---

## Backend Changes Summary

### API
- `POST /sources` — remove `standalone` param, always enqueue cluster pipeline
- `GET /ideas` — new endpoint returning `generated=false, superseded=false` show ideas for the user
- `GET /ideas/:id/outline` — trigger outline generation for a specific idea (idempotent — returns cached if already done)
- `POST /ideas/:id/generate` — create episode row from idea, enqueue `generate_episode` job
- `DELETE /jobs/:id` — cancel a queued job (only if `status=queued`)

### Worker
- `handle_ingest` — remove standalone path, always enqueue `generate_ideas`
- `generate_ideas` — add invalidation step after writing new ideas (mark superseded)
- `generate_episode` — if episode row already has `outline` populated, skip `outlining` stage

### Database
- Migration: add `title`, `description`, `duration_estimate_seconds`, `outline`, `chapters`, `superseded` to `show_idea`

---

## Frontend Changes Summary

- New **Ideas tab** — list of idea cards
- New **Idea Detail screen** — outline preview + Generate button
- `POST /sources` call — drop `standalone` param
- Episode generation flow entry point moves from source ingest to idea detail page
- Existing generating/player screen reused once episode is created
- Cancel button on episode cards when `status=queued`

---

## Outline Job — API Pattern

Consistent with existing ingest and episode generation patterns:

- `POST /ideas/:id/outline` → enqueues an `outline_idea` job → returns `job_id` immediately
- Client polls `GET /jobs/:id` every **5 seconds** until `status=done`
- On done, client fetches `GET /ideas/:id` → outline fields now populated → renders detail page
- Idempotent: if `show_idea.outline` already populated, job completes instantly returning cached result
- Outline jobs get higher priority than `generate_episode` in `_JOB_PRIORITY` so they don't sit behind long synthesis jobs

New job type: `outline_idea`, payload: `{ "idea_id": "<uuid>", "user_id": "<uid>" }`

---

## Customisation (Remix Sheet)

- On the detail page, **Customise** (left button) opens the existing remix sheet, adapted
- User can change: format (Drift/Unpack/Quickie/Explore) + angle (free-form text input)
- Tapping "Save Changes" saves to **local component state only** — not persisted to DB
- Closing the detail sheet loses all customisation — this is intentional (Option 1)
- When Generate is tapped, overrides from local state are passed to `POST /ideas/:id/generate`
- If user never opened Customise, defaults are used (LLM-chosen format + original angle)

### Customisation invalidates the cached outline

The outline is generated from the **briefing packet**, which bakes in the format's config
(segment count, pacing, energy curve, resolution style) and the editorial direction (angle).
A cached outline is therefore only valid for the format + angle it was built with.

| What the user changed | Cached outline reusable? | Episode path |
|---|---|---|
| Nothing (defaults) | Yes | Copy outline → skip `outlining` |
| Format | No | Full path incl. `outlining` |
| Angle | No | Full path incl. `outlining` |
| Length | No | Full path incl. `outlining` |
| Speaker(s) only | Yes | Copy outline → skip `outlining` |

Speakers only affect transcript + TTS, never the outline — so speaker changes reuse the cache.

Accepted side effect: a customised episode's final title/chapters may differ from what the
detail sheet showed — the user changed the recipe, so the preview no longer binds.

---

## Ideas Tab UI

- New **4th tab** in the app
- **Grid of idea cards** (thumbnail style)
- Each card:
  - Background: format gradient (same gradients used for format thumbnails)
  - Bottom portion fades/greys out
  - Format icon + format name above the angle text
  - Angle text as description
  - Source stack at the bottom (similar to source chips on episode cards)
- Empty state: nothing shown while sources are ingesting — ideas appear when cluster pipeline writes them
- Ordering: newest first by `created_at`

---

## Idea Detail Page UI

- **Full sheet modal** on card tap
- Outline polling starts immediately on sheet open (`POST /ideas/:id/outline` fired on open)
- Sheet sections:
  - Sources used (top)
  - Outline / chapters — skeleton state while polling, fills in once outline job completes (~5-10s)
  - Other metadata: title, description, duration estimate
- Bottom of sheet — two buttons:
  - **Customise** (left) → opens remix sheet
  - **Generate Episode** (right) → fires `POST /ideas/:id/generate`
- Outline failure: error state + "Try again" button (re-calls `POST /ideas/:id/outline`)

---

## Seamless Pipeline Handoff

No cross-job coordination. Both jobs are idempotent and the episode pipeline is self-sufficient:

**`outline_idea` job:**
- Before running, checks `show_idea.outline` — if already populated, no-op (instant done)
- `POST /ideas/:id/outline` is idempotent — if a job for this idea is already queued/running,
  it returns the existing `job_id` instead of enqueuing a duplicate

**`generate_episode` job (new-flow episodes):**
- On start, checks `show_idea.outline`
- If populated (and no outline-invalidating customisation) → copies it → starts at `transcribing`
- If not populated → runs the `outlining` stage itself (existing code path, unchanged)

**Scenario A — Generate tapped after outline is done:** episode copies the cached outline, skips `outlining`.

**Scenario B — Generate tapped while outline job is still in flight:** episode is enqueued;
because outline jobs have higher queue priority and take ~10s, the outline almost always lands
before `generate_episode` is dequeued — the worker finds it cached and skips `outlining`.
In the rare race where it hasn't landed, the episode just runs its own outline. Worst case:
one redundant ~10s LLM call. No `pending_outline` flags, no cross-job signaling, no race windows.

UX is identical in both scenarios: the user tapped Generate before seeing an outline, so
whichever outline wins is the one they experience.

---

## Superseded-While-Viewing Race

A user can have the detail sheet open for idea X while a background cluster run supersedes it
(new article finished ingesting, better cluster written).

**Decision: allow Generate on a superseded idea.**
- `POST /ideas/:id/generate` does **not** check the `superseded` flag
- `superseded` only controls list visibility (`GET /ideas` filters it out)
- The row still holds everything generation needs (`angle`, `source_ids`, cached outline)
- The user gets exactly the episode they were looking at — no invisible rug-pull

Accepted wrinkle: the user may later also generate the replacement idea (e.g. `[A2, A4, A6]`
after generating `[A2, A4]`) and get two similar episodes. That's a visible, self-inflicted
choice — better than blocking a decision they already made.

---

## Streaming Integration

The entire streaming stack (split state machine, incremental HLS publishing, playlist endpoint,
frontier scrubber, estimate→exact upgrade) sits **downstream of episode-row creation** and does
not care who created the row. It carries over unchanged.

```
Generate tapped → queued → [outlining]? → transcribing → script_ready (PLAYABLE, HLS live)
              → synthesizing (chunks publishing) → ready (exact duration + chapters)
```

Touchpoints:

1. **Post-Generate destination** — sheet closes → episode card appears in Shows with the existing
   generating state → becomes playable at `script_ready` exactly like today. Zero new player work.
2. **Duration estimate now has three fidelity levels:**
   - Outline-based (idea detail page) — NEW estimator, computed from format `target_words`
     (the existing `_estimate_duration_seconds` needs a transcript, which doesn't exist yet)
   - Transcript-based at `script_ready` — existing
   - Exact at `ready` — existing
   Frontend already handles estimate→exact; it just seeds one step earlier now.
3. **Chapters** — detail sheet shows provisional outline chapters; the streaming player hides
   chapters while synthesizing and snaps real ones in at `ready`. Already built, no conflict.
4. **`outlining` status returns** for customised episodes — episode card generating state
   already handles it (status exists today).

---

## Seed Exception (deliberate)

The seed short-circuit in `handle_ingest` (known onboarding URLs → instantly copy a pre-baked
source + episode to the user) **bypasses the ideas flow entirely and stays as-is**. It
contradicts "user always chooses" on purpose: onboarding needs instant gratification.
Recorded here so it's a decision, not an accident.

---

## Implementation Status (first pass complete)

### Backend (curia-v2, branch `streaming`)
- ✅ Migration `0028_show_idea_outline_cache` — new columns + partial index on live ideas
- ✅ `idea_generator.py` — `auto_generate` removed; `supersede_stale` node added; `diff_clusters` skips only **non-superseded** exact matches (a superseded cluster reappearing gets a fresh idea)
- ✅ `handle_ingest` — standalone path removed; always enqueues `generate_ideas`; dedup now checks **queued** jobs only (a running cluster job loaded the archive before this source was ready, so it must not absorb the dedup)
- ✅ `worker/handlers/outline_idea.py` — new job; idempotent; outline-time duration estimator from format `target_words` (~6 chars/word ÷ TTS chars/sec + intro)
- ✅ `generate_episode_script` — uses cached outline from the episode row when present, skips the outline LLM call
- ✅ API: `GET /ideas`, `GET /ideas/:id`, `POST /ideas/:id/outline`, `POST /ideas/:id/generate`, plus schemas
- ✅ Queue priority: `outline_idea` = 3 (between `generate_ideas` and `generate_episode`)

### Frontend (curia-frontend, branch `streaming`)
- ✅ `services/api.ts` — ShowIdea type + `ideas/idea/requestOutline/jobStatus/generateFromIdea/deleteEpisode`; `addSource` drops `standalone` (all 3 call sites updated)
- ✅ `components/IdeasView.tsx` — new self-contained Ideas tab: 2-col grid (format gradient thumb, bottom fade, format icon+label, angle, favicon source stack), detail sheet (outline skeleton → filled via 5s job polling, sources, episode plan, error + Try again), customise overlay (format picker + angle input, local state only), Generate with overrides
- ✅ `studio.tsx` — third internal tab "Ideas" (lightbulb icon in the bottom bar between Shows and the + button), header label crossfade, pane animations generalised to 3 views
- ✅ Cancel queued episodes — listed in the GeneratingDetailOverlay with a cancel action per queued episode

### Deviations from the plan (deliberate)
1. **`DELETE /episodes/:id` instead of `DELETE /jobs/:id`** — the frontend has the episode id on the card, not the job id. One endpoint covers cancel-when-queued (atomic job-cancel race with the worker, idea flipped back to `generated=false`) AND plain delete for ready/failed episodes. Mid-generation → 409.
2. **Cancel UI lives in the generating overlay**, not on episode cards — queued episodes don't render as cards in the Shows list; they appear via the GeneratingPill → overlay.
3. **Supersede rule refined** — implemented as "live idea whose exact source set is no longer among the clusters produced by the current run" (handles growth + reshuffles, never falsely retires overlapping-but-valid clusters like [A1,A3] + [A1,A5]).
4. **`standalone` kept in the API schema** but ignored — old app builds still send it; dropping the field would 422 them.
5. **Customise sheet is format + angle only** for now — length/speaker pickers can be added later (backend already accepts them on `POST /ideas/:id/generate`).

### ⚠️ Deploy coupling
The shipped App Store build (1.0.6) expects auto-episode generation — save URL → episode appears. Deploying this backend to prod would silently break that UX for shipped clients (URLs would produce ideas, which 1.0.6 has no screen for). **Backend and frontend must ship together** as the next app version.

---

## Remaining / Next

1. **Run migration 0028** against the dev DB (`alembic upgrade head` in docker-compose).
2. **E2E test** — save URL → cluster → idea appears → tap → outline skeleton → filled → Generate → episode streams at `script_ready` → cancel path.
3. **Outline estimate calibration** — compare outline-time estimates against transcript-time estimates on a few real episodes.
4. **Ideas tab polish after first run** — card image assets (user mentioned custom background images later), ordering beyond newest-first.
