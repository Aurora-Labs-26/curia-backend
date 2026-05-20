# Auto-Generation Design
## Idea Pipeline → Instant Episode Creation

**Last updated:** 2026-05-17  
**Status:** Design / pre-implementation

---

## 1. Problem Statement

The current pipeline requires manual intervention at two points:

1. User must trigger "generate ideas" manually - generate show ideas
2. User must pick an idea and manually trigger "create episode" - generate episode pipeline

The goal is to remove both manual steps. When new bookmarks arrive, episodes should appear in the user's feed automatically — no taps required.

---

## 2. Current Pipeline (as-is)

```
User adds bookmark
  → POST /sources
  → enqueue("ingest", {source_id, url})

Worker picks up ingest job
  → scrapes URL, extracts insights, writes source_primitive_embedding
  → source.status = "ready"

[MANUAL] Someone triggers generate_ideas
  → enqueue("generate_ideas", {user_id})

Worker picks up generate_ideas job
  → LangGraph: load_archive → cluster_sources → evaluate_ideas → filter_covered → save_ideas
  → Deletes all ungenerated show_ideas for user
  → Writes fresh ideas to show_idea table

[MANUAL] User opens app, picks an idea, hits "Remix"
  → POST /episodes with show_name, editorial_direction, length_minutes, speaker
  → enqueue("generate_episode", {episode_id, user_id})

Worker picks up generate_episode job
  → studio.generator.process_episode()
  → Writes title, transcript, audio_path
  → episode.status = "ready"
```

---

## 3. Target Pipeline (to-be)

```
User adds bookmark
  → POST /sources
  → enqueue("ingest", {source_id, url})

Worker: ingest completes
  → source.status = "ready"
  → enqueue("generate_ideas", {user_id})          ← NEW: auto-trigger after ingest

Worker: generate_ideas runs
  → load_archive → cluster_sources → diff_clusters  ← NEW node
  → evaluate_ideas (only for new clusters)
  → filter_covered
  → save_ideas (append only, skip duplicates)
  → auto_generate (NEW node: create episodes for each new idea)

Worker: generate_episode runs (one per new idea)
  → studio.generator.process_episode()
  → episode.status = "ready"

User opens app → episodes already in feed, generating or ready
```

---

## 4. Design Decisions

### 4.1 Trigger: who fires generate_ideas?

**Decision:** The ingest worker fires it automatically after each source reaches `ready`.

**Where:** `worker/handlers/ingest.py` — after marking source ready, call:
```python
await enqueue("generate_ideas", {"user_id": user_id}, user_id=user_id)
```

**Implication:** If a user adds 5 bookmarks in rapid succession, 5 `generate_ideas` jobs are enqueued. This is fine because:
- The diff node (section 4.3) makes repeated runs idempotent — no duplicate episodes
- The queue serialises them naturally (FIFO, one worker)
- Future optimisation: deduplicate by user_id before picking up (out of scope for now)

---

### 4.2 delete-and-rewrite → append-only

**Current `save_ideas`:**
```python
DELETE FROM show_idea WHERE user_id = $user_id AND generated = false
# then INSERT all ideas
```

**Problem in the new flow:** By the time a second run fires, ideas from the first run are still `generated = false` — the auto_generate node marks them `generated = true` **before** enqueueing, but there's a brief window between INSERT and UPDATE. The DELETE would wipe them.

**Decision:** Remove the DELETE. `save_ideas` only inserts. Deduplication is handled by the diff node (section 4.3), which ensures we never write an idea whose source_id set already exists.

**Updated `save_ideas`:** Remove the `DELETE FROM show_idea` line entirely.

---

### 4.3 New node: `diff_clusters`

Sits between `cluster_sources` and `evaluate_ideas`.

**Purpose:** Filter out clusters whose source_id set already exists in `show_idea` for this user — either generated or ungenerated. If a cluster is unchanged, skip it entirely (no LLM call, no write).

**Logic:**
```python
async def diff_clusters(state: IdeaGenState) -> IdeaGenState:
    user_id = state["user_id"]
    clusters = state["clusters"]

    # Fetch all existing idea source_id sets for this user
    existing = await db_query(
        "SELECT source_ids FROM show_idea WHERE user_id = $user_id",
        {"user_id": user_id}
    )
    existing_sets = [frozenset(str(s) for s in row["source_ids"]) for row in (existing or [])]

    new_clusters = []
    for cluster in clusters:
        cluster_set = frozenset(str(s) for s in cluster)
        if cluster_set not in existing_sets:
            new_clusters.append(cluster)

    logger.info(f"[diff_clusters] {len(clusters)} clusters → {len(new_clusters)} new after diff")
    return {**state, "clusters": new_clusters}
```

**Why this is sufficient (no stale flag needed):**

The cluster algorithm is holistic — it runs over ALL sources every time. When a new source H joins existing cluster [A,B,C] to form [A,B,C,H]:
- [A,B,C] is a strict subset — it will NOT be produced as a maximal clique (the maximality filter removes it)
- [A,B,C,H] is the new cluster, and its source_id set is different from [A,B,C] → passes the diff
- The old idea for [A,B,C] already has an episode (generated=true) — it stays in the table as history
- The new idea for [A,B,C,H] gets written and generates a new episode

Two episodes, different source sets, both valid. No stale flag needed.

---

### 4.4 New node: `auto_generate`

Sits after `save_ideas`.

**Purpose:** For each idea just written, create an episode row and enqueue generation.

**Why a separate node (not inside save_ideas):**
- `save_ideas` should do one thing: write ideas
- `auto_generate` is a separate concern: create episodes
- Easier to disable (remove the node from the graph) for a "review before generating" mode later
- Cleaner retry boundary — if episode creation fails, save_ideas already committed

**Logic:**
```python
async def auto_generate(state: IdeaGenState) -> IdeaGenState:
    user_id = state["user_id"]

    # Fetch the ideas just written (ungenerated, created in last 60s)
    new_ideas = await db_query(
        """
        SELECT id, format, source_ids, angle
        FROM show_idea
        WHERE user_id = $user_id
          AND generated = false
          AND created_at > now() - interval '60 seconds'
        """,
        {"user_id": user_id}
    )

    for idea in (new_ideas or []):
        # Validate format before using it
        from studio.formats import FORMATS
        fmt = idea["format"]
        if fmt not in FORMATS:
            logger.warning(f"[auto_generate] Unknown format {fmt!r} for idea {idea['id']}, defaulting")
            fmt = "clarity_engine"

        # Create the episode row
        episode_id = str(uuid.uuid4())
        await db_execute(
            """
            INSERT INTO episode
                (id, user_id, show_name, show_idea_id, editorial_direction,
                 length_minutes, speaker_override, status)
            VALUES
                ($id::uuid, $user_id, $show, $idea_id::uuid, $direction,
                 NULL, NULL, 'queued')
            """,
            {
                "id": episode_id,
                "user_id": user_id,
                "show": fmt,
                "idea_id": str(idea["id"]),
                "direction": idea.get("angle", ""),
            },
        )

        # Mark idea as generated (before enqueueing — avoids delete race)
        await db_execute(
            "UPDATE show_idea SET generated = true WHERE id = $id::uuid",
            {"id": str(idea["id"])}
        )

        # Enqueue generation
        await enqueue(
            type="generate_episode",
            payload={"episode_id": episode_id, "user_id": user_id},
            user_id=user_id,
        )
        logger.info(f"[auto_generate] Enqueued episode {episode_id} for idea {idea['id']}")

    return {**state, "auto_generated_count": len(new_ideas or [])}
```

---

### 4.5 Format validation

**Problem:** The LLM picks the format. It is constrained by prompt to return one of four canonical names, but LLMs can hallucinate or return variants (`"clarity-engine"`, `"ClarityEngine"`, etc).

**Decision:** Validate in `auto_generate` before inserting the episode row. If the format is invalid, fall back to `"clarity_engine"`.

**Also add validation in `save_ideas`** so the bad string never reaches the DB:
```python
from studio.formats import FORMATS
fmt = idea.get("format", "")
if fmt not in FORMATS:
    fmt = "clarity_engine"
```

---

## 5. Updated LangGraph

```
load_archive
    ↓
cluster_sources
    ↓
diff_clusters          ← NEW: filter clusters already in show_idea table
    ↓
evaluate_ideas         (only runs on new clusters)
    ↓
filter_covered
    ↓
save_ideas             (append only — no DELETE)
    ↓
auto_generate          ← NEW: create episode rows + enqueue jobs
    ↓
END
```

If `diff_clusters` returns zero new clusters, all downstream nodes receive an empty list and return immediately. The run is cheap — one DB read.

---

## 6. Implementation Plan

### Step 1 — Ingest trigger
- **File:** `worker/handlers/ingest.py`
- After source reaches `ready` status, enqueue `generate_ideas`

### Step 2 — Remove DELETE from save_ideas
- **File:** `intelligence/idea_generator.py`
- Remove `DELETE FROM show_idea WHERE user_id = $user_id AND generated = false`
- Add format validation before INSERT

### Step 3 — Add diff_clusters node
- **File:** `intelligence/idea_generator.py`
- New async function `diff_clusters`
- Wire into graph between `cluster_sources` and `evaluate_ideas`

### Step 4 — Add auto_generate node
- **File:** `intelligence/idea_generator.py`
- New async function `auto_generate`
- Add `auto_generated_count` to `IdeaGenState`
- Wire into graph after `save_ideas`

### Step 5 — Update IdeaGenState
- **File:** `intelligence/idea_generator.py`
- Add `auto_generated_count: int` field

### Step 6 — CHANGELOG
- Log all changes per `CLAUDE.md` rules

---

## 7. What Remains After This Plan

### 7.1 Real audio playback (biggest gap)

`PlaybackContext.tsx` runs a fake timer (`setInterval` ticking progress every second). There is no actual audio. `expo-av` is not wired up.

**What needs to happen:**
- Install and configure `expo-av`
- Replace the fake timer with `Audio.Sound` — load from `GET /episodes/{id}/audio`
- Handle audio focus, background playback, lock screen controls
- Wire actual `onPlaybackStatusUpdate` to progress state
- The backend `GET /episodes/{id}/audio` returns a `FileResponse` from local disk — this works for local dev but needs a CDN/R2 URL for production

**This is the single biggest gap between current app and a working product.**

---

### 7.2 Remix screen not wired to backend

`app/remix/[id].tsx` uses `MOCK_SHOWS` to pre-populate state and the "Remix Show" button calls `fadeAndClose()` — it does nothing. No API call is made.

**What needs to happen:**
- Fetch the real episode via `api.episode(id)` to pre-populate format/host/duration
- Map duration (minutes integer) to the DURATIONS strings
- Map host name back to the HOSTS array
- "Remix Show" button → `POST /episodes` with selected format, host (as speaker), duration, angle as editorial_direction
- Navigate to the new episode's now-playing screen on success
- Handle loading / error states

---

### 7.3 Share screen not wired to backend

`app/share/index.tsx` fetches page metadata from microlink.io (third-party) but never calls `POST /sources`. The bookmark is never saved.

**What needs to happen:**
- After `fetchPageMeta` resolves, call `api.addSource(url)` (method not yet in `services/api.ts`)
- Add `addSource` to `api` object in `services/api.ts`
- Handle the case where the URL is already in the user's pile (409 conflict from backend)
- Show a different confirmation message if it's a duplicate

---

### 7.4 Sources / Pile screen not wired

The index tab presumably shows bookmarks. Need to verify it calls `api.sources()` and handles loading/error states, not mock data.

---

### 7.5 Playback progress not persisted

`PUT /episodes/{id}/progress` exists on the backend. It's never called from the frontend. When the user replays an episode, their position is lost.

**What needs to happen:**
- In `PlaybackContext`, call `api.updateProgress(id, progress)` periodically (every 10s) and on pause/complete
- Add `updateProgress` to `services/api.ts`
- On load in now-playing, seed progress from `show.progress` (already mapped in `mapEpisode`)

---

### 7.6 Player screen (`player/[id].tsx`) still uses MOCK_SHOWS

`app/(tabs)/player/[id].tsx` imports `MOCK_SHOWS` to find the episode. This needs to fetch from `api.episode(id)`.

---

### 7.7 Audio serving architecture

The backend serves audio via `FileResponse` from local disk. This works on localhost but breaks in production because:
- Files don't survive container restarts
- No CDN, no range request support (needed for audio scrubbing)
- Large files block the FastAPI process

**What needs to happen (not immediate, but before any real hosting):**
- Store audio on Cloudflare R2 (or S3)
- Return a signed URL from `GET /episodes/{id}/audio` instead of streaming the file directly
- Frontend loads audio from the CDN URL, not from the API server

---

### 7.8 Onboarding / KB not connected to generation

The onboarding screen and `profile/index.tsx` presumably collect user preferences (interests, dislikes, preferred formats). These get stored in the KB. The idea generator already reads the KB (`load_kb` in `load_archive`). But it's unclear whether:
- Onboarding actually saves to `POST /kb` on the backend
- The KB format the frontend writes matches what the idea generator reads

This needs an audit before trusting personalisation.

---

### 7.9 No empty state for fresh users

A brand new user with zero bookmarks and zero episodes sees... nothing. The app needs:
- Empty state on the shows tab ("Add some bookmarks to get started")
- Empty state on the pile tab
- Clear onboarding path from first open → share first link → see first episode generating

---

### 7.10 Error recovery UX

Currently, failed episodes show a red "Failed to generate" badge. There's no retry button. The user is stuck.

**What needs to happen:**
- Add a retry action on failed episode cards → calls `POST /episodes` with same parameters
- Or surface a "report" action so the user can flag bad outputs

---

## 8. Priority Order (after auto-generation plan)

| Priority | Item | Why |
|----------|------|-----|
| 1 | Real audio playback (expo-av) | App is not usable without it |
| 2 | Share screen → POST /sources | Bookmarks never saved |
| 3 | Remix screen → POST /episodes | Manual episode creation broken |
| 4 | Playback progress persistence | Basic UX expectation |
| 5 | Player screen mock removal | Same issue as now-playing |
| 6 | Audio on R2 / CDN | Needed before any hosted environment |
| 7 | Empty states | Needed for any real user |
| 8 | KB / onboarding audit | Needed for personalisation to work |
| 9 | Retry on failed episodes | Polish |
////


1. to discuss
	1. episode generation is sequentially or parallel
		1. parallel