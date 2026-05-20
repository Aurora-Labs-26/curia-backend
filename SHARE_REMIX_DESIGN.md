# Share Sheet & Remix Sheet — Design Document
**Last updated:** 2026-05-17  
**Status:** Design / pre-implementation

---

## Part 1 — Share Sheet

### Overview

The share sheet is a modal overlay launched from the iOS/Android share extension. The user is in another app (Safari, Twitter, etc.) and shares a URL into Curia. The modal appears on top of their current app. When dismissed — whether by auto-timer, manual tap, or "Queue It" — the modal closes and they return to wherever they were. There is no navigation to the Curia feed.

### Two screens

```
share/index.tsx       — confirmation toast (auto-dismisses after 5s)
share/customize.tsx   — optional config sheet (format, host, duration, angle, mix level)
```

---

### Screen 1: `share/index.tsx`

#### What it does today
- Receives `url` as a query param
- Fetches page title + favicon from microlink.io
- Shows a toast: "Good find! It's in your queue."
- Auto-dismisses after 5 seconds
- "Customize" button navigates to `share/customize.tsx`

#### What needs to change
**`POST /sources` fires immediately on mount, before anything else.**

The act of sharing = the act of saving. The source is added to the pile regardless of whether the user dismisses, waits, or customizes. There is no confirmation gate.

```
share/index.tsx mounts
  → api.addSource(url) fires immediately (fire and forget)
  → fetchPageMeta(url) runs in parallel (for display only)
  → toast renders as normal
```

If `addSource` fails (duplicate URL, network error): show a subtle error variant of the toast — "Already in your pile" or "Couldn't save this one." The auto-dismiss and "Customize" path still work.

#### Sequence

```
User: taps Share in Safari, picks Curia

share/index.tsx mounts
  ├── api.addSource(url)           → POST /sources         [fires immediately]
  └── fetchPageMeta(url)          → microlink.io           [for display only]

Toast shows: "Good find! It's in your queue."

Backend (async, user doesn't wait):
  → source row created, status = "fetching"
  → ingest worker scrapes URL
  → source.status = "ready"
  → generate_ideas job enqueued automatically
  → idea generator runs: cluster → diff → evaluate → save_idea → auto_generate
  → episode row created, status = "queued"
  → generate_episode worker runs
  → episode.status = "ready"

User never sees any of this. They're back in Safari.
Next time they open Curia → episode is in the feed.

--- Path A: User dismisses (tap or auto-timer) ---

Modal closes → user returns to Safari
Nothing else fires. Backend handles everything.

--- Path B: User taps "Customize" ---

Navigates to share/customize.tsx
(see below)
```

---

### Screen 2: `share/customize.tsx`

#### What it does today
- Config tiles: format, host, duration, angle
- Mix meter: "Just this / Balanced / Maximum connections"
- "Queue It" button → `fadeAndClose()` — nothing happens

#### Conceptual model

The customize screen has **two layers**:

**Layer 1 — Mix meter (scope of the episode)**
Controls whether this source becomes a standalone episode or gets clustered with the user's pile.

| Mix value | Label | Meaning |
|-----------|-------|---------|
| 0.0 | Just this | Standalone — only this URL, skip clustering |
| 0.5 | Balanced | Let auto-clustering run — may connect with pile |
| 1.0 | Maximum connections | Same as Balanced (future: weight toward more connections) |

> Balanced and Maximum connections are treated identically in the backend today. The distinction is a UX affordance — users feel more in control. Future work can differentiate them.

**Layer 2 — Config tiles (how the episode is made)**
Override any of: format, host, duration, angle. All optional — defaults are used if not changed.

- `format` → maps to `show_name` in `POST /episodes`
- `host` → maps to `speaker` in `POST /episodes`
- `duration` → maps to `length_minutes` (parse "20 min" → 20)
- `angle` → **appended** to the LLM-generated `editorial_direction`

The angle append logic: even on the standalone path, the `evaluate_ideas` LLM still runs on the source and generates its own angle. The user's angle refinement is appended to that, not a replacement. So the episode generator sees: `[LLM angle]. [User's angle override].`

---

#### Path 1 — "Just this" (mix = 0.0)

Skip clustering. Treat the source as a standalone group, run through `evaluate_ideas`, create one episode with config overrides.

**"Queue It" fires:**
```
POST /generate_from_source   (new endpoint, or a job type)
  payload: {
    source_id:           <id of the source saved in index.tsx>,
    standalone:          true,
    show_name:           <selected format's backend name, or null for default>,
    speaker:             <selected host lowercased, or null>,
    length_minutes:      <parsed from duration string, or null>,
    angle_override:      <angle text, or null>,
  }
```

Backend job `generate_from_source`:
```
1. Load source + its insights
2. evaluate_ideas on this single source group
   → LLM produces angle + format (if user didn't override format, use LLM's)
3. Write show_idea (generated = true immediately, since we're going straight to episode)
4. Create episode row with:
   - show_name = user override ?? LLM format
   - speaker = user override ?? null
   - length_minutes = user override ?? null
   - editorial_direction = llm_angle + (". " + angle_override if provided)
5. Enqueue generate_episode job
```

Modal closes. User returns to their previous app.

**Sequence:**
```
User picks "Just this", sets format=Sharp Take, angle="focus on the regulatory angle"

share/customize.tsx:
  "Queue It" pressed
  → POST /generate_from_source {source_id, standalone: true, show_name: "clarity_engine",
                                 angle_override: "focus on the regulatory angle"}
  → fadeAndClose()
  → modal closes, user back in Safari

Backend:
  → evaluate_ideas on source alone
    LLM produces: angle = "The hidden cost of algorithmic pricing"
  → episode created:
    editorial_direction = "The hidden cost of algorithmic pricing. focus on the regulatory angle"
    show_name = "clarity_engine"
  → generate_episode runs
  → episode ready in feed
```

---

#### Path 2 — "Balanced / Maximum connections" (mix = 0.5 or 1.0)

Let auto-clustering run. The new source is included in the full pile clustering. One or more clusters will contain this source (at minimum a standalone cluster if no connections found — so at least one episode is always created).

**"Queue It" fires:**
```
POST /generate_from_source
  payload: {
    source_id:           <id of the source saved in index.tsx>,
    standalone:          false,
    show_name:           <selected format's backend name, or null>,
    speaker:             <selected host lowercased, or null>,
    length_minutes:      <parsed from duration string, or null>,
    angle_override:      <angle text, or null>,
  }
```

Backend job `generate_from_source`:
```
1. Run full idea generator pipeline:
   load_archive → cluster_sources → diff_clusters → evaluate_ideas → save_ideas
   (same as the auto-generation pipeline, but triggered explicitly for this user)
2. For each new idea written that contains this source_id:
   - Create episode row with config overrides applied
   - editorial_direction = llm_angle + (". " + angle_override if provided)
   - show_name / speaker / length_minutes = user overrides ?? idea defaults
3. Enqueue generate_episode jobs
```

Modal closes. User returns to their previous app.

**Sequence:**
```
User's pile already has sources A (tech regulation), B (EU antitrust)
User shares C (new article: "Apple fined €500m by EU")
User picks "Balanced", no config overrides, no angle

share/index.tsx:
  → api.addSource(url_C)  [C saved immediately]

share/customize.tsx:
  User picks "Balanced", taps "Queue It"
  → POST /generate_from_source {source_id: C, standalone: false}
  → fadeAndClose()

Backend:
  → load_archive: sources A, B, C
  → cluster_sources: finds cluster [A, B, C] (all score ≥ 0.70)
  → diff_clusters: [A, B] existed before, [A, B, C] is new → passes through
  → evaluate_ideas on [A, B, C]:
    LLM produces: angle = "Why Apple's fine is the opening shot of a decade-long tech war"
                  format = "exploration_engine"
  → save_ideas: show_idea written, generated = true immediately
  → episode created:
    editorial_direction = "Why Apple's fine is the opening shot of a decade-long tech war"
    show_name = "exploration_engine"  (no user override)
  → generate_episode runs
  → episode ready in feed
```

**Edge case — no connections found:**
```
User's pile has sources D (cooking), E (gardening)
User shares C (Apple EU fine)
User picks "Balanced"

→ cluster_sources: no clique containing C (similarity < 0.70 with D, E)
→ C falls through as standalone
→ evaluate_ideas on [C] alone: produces one idea
→ one episode created

Result: user still gets one episode. "Balanced" behaved like "Just this."
This is acceptable for now. Future work: surface this to the user ("No connections found, 
created a standalone episode").
```

---

## Part 2 — Remix Sheet

### Overview

Remix is triggered from inside the Curia app — from a show card or the now-playing screen. It takes an existing episode and creates a new one using the **same source_ids** but with different configuration. There is no mix meter — the scope is always "same sources."

After "Remix Show" is tapped, the remix modal dismisses and the user is taken to the now-playing screen for the new episode (since they're already inside the Curia app, navigation makes sense here unlike the share sheet).

### Screen: `remix/[id].tsx`

#### What it does today
- Receives episode `id` as route param
- Pre-populates config from `MOCK_SHOWS.find(s => s.id === id)` — fake data
- Config tiles: format, host, duration, angle
- "Remix Show" button → `fadeAndClose()` — nothing happens

#### What needs to change

1. **Fetch real episode on mount** via `api.episode(id)` to pre-populate tiles
2. **Map values back to picker options:**
   - `show.format` (frontend slug e.g. `"sharp-take"`) → pre-select in format picker
   - `show.host` (e.g. `"Kenji"`) → pre-select in host picker
   - `show.duration` (integer minutes) → snap to nearest DURATIONS string
3. **"Remix Show" button** → `POST /episodes` with new config, navigate to `now-playing/[newId]`
4. **Loading state** on button while request is in flight
5. **Remove `MOCK_SHOWS` import**

#### Config mapping

```typescript
// Frontend slug → backend show_name
const FORMAT_TO_BACKEND: Record<ShowFormat, string> = {
  "slow-burn":    "narrative_drift",
  "sharp-take":   "clarity_engine",
  "live-wire":    "momentum_loop",
  "open-verdict": "exploration_engine",
};

// Duration string → length_minutes
function parseDuration(d: string): number {
  return parseInt(d.split(" ")[0], 10);  // "20 min" → 20
}

// Snap show.duration (int minutes) to nearest DURATIONS string
function snapDuration(minutes: number): string {
  const options = [10, 20, 30];
  const nearest = options.reduce((a, b) =>
    Math.abs(b - minutes) < Math.abs(a - minutes) ? b : a
  );
  return `${nearest} min`;
}
```

#### Editorial direction on remix

Same rule as share: the user's angle is **appended** to the original episode's `editorial_direction`, not a replacement.

```typescript
const editorialDirection = [
  originalEpisode.description ?? "",   // LLM-generated angle from original idea
  selectedAngle ?? "",                  // user's new angle override
].filter(Boolean).join(". ");
```

#### "Remix Show" → `POST /episodes`

```typescript
const res = await api.createEpisode({
  showName:           FORMAT_TO_BACKEND[selectedFormat],
  speaker:            selectedHosts[0].toLowerCase(),
  lengthMinutes:      parseDuration(selectedDuration),
  editorialDirection: editorialDirection,
  // Note: source_ids are NOT passed — the backend re-uses the original show_idea's sources
  // We achieve this by passing show_idea_id from the original episode
  showIdeaId:         originalEpisode.showIdeaId ?? undefined,
});
router.replace(`/now-playing/${res.id}`);
```

> **Note:** `show_idea_id` links the new episode back to the original idea's source set. If the original episode has no `show_idea_id` (e.g. it was created manually), the backend falls back to using all sources for that user — same as a fresh generation. This is acceptable.

---

#### Sequence

```
User is on the now-playing screen for episode E1:
  Title: "Why Apple's fine is the opening shot of a decade-long tech war"
  Format: Sharp Take, Host: Kenji, Duration: 12 min

User taps "Remix"

remix/[id].tsx mounts:
  → api.episode(id)  fetches E1
  → pre-populates tiles:
      Format: Sharp Take  (from show.format = "sharp-take")
      Host: Kenji         (from show.host = "Kenji")
      Duration: 10 min    (snap 12 → nearest = 10)
      Angle: —            (blank by default)

User changes:
  Host: Arjun
  Duration: 20 min
  Angle: "make it more accessible, less jargon"

User taps "Remix Show":
  → button shows loading spinner

  POST /episodes {
    show_name:           "clarity_engine",
    speaker:             "arjun",
    length_minutes:      20,
    editorial_direction: "Why Apple's fine is the opening shot of a decade-long tech war. make it more accessible, less jargon",
    show_idea_id:        <original episode's show_idea_id>
  }

  Backend:
    → episode row created, status = "queued"
    → generate_episode job enqueued
    → returns { id: "new-episode-uuid", status: "queued" }

  → router.replace("/now-playing/new-episode-uuid")
  → now-playing screen shows loading state for new episode
  → polling kicks in, episode transitions to "ready" when done
```

---

## Part 3 — New API additions needed

### `services/api.ts`

```typescript
// Add a source to the pile
addSource: async (url: string): Promise<{ id: string; status: string }> => {
  return request<{ id: string; status: string }>("/sources", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
},

// Create an episode directly (used by remix)
createEpisode: async (params: {
  showName: string;
  speaker?: string;
  lengthMinutes?: number;
  editorialDirection?: string;
  showIdeaId?: string;
}): Promise<{ id: string; status: string }> => {
  return request<{ id: string; status: string }>("/episodes", {
    method: "POST",
    body: JSON.stringify({
      show_name:            params.showName,
      speaker:              params.speaker ?? null,
      length_minutes:       params.lengthMinutes ?? null,
      editorial_direction:  params.editorialDirection ?? "",
      show_idea_id:         params.showIdeaId ?? null,
    }),
  });
},

// Trigger generation from a single source (used by share customize)
generateFromSource: async (params: {
  sourceId: string;
  standalone: boolean;
  showName?: string;
  speaker?: string;
  lengthMinutes?: number;
  angleOverride?: string;
}): Promise<{ job_id: string }> => {
  return request<{ job_id: string }>("/generate-from-source", {
    method: "POST",
    body: JSON.stringify({
      source_id:      params.sourceId,
      standalone:     params.standalone,
      show_name:      params.showName ?? null,
      speaker:        params.speaker ?? null,
      length_minutes: params.lengthMinutes ?? null,
      angle_override: params.angleOverride ?? null,
    }),
  });
},
```

### Backend: new endpoint + job type needed

`POST /generate-from-source` — a new FastAPI route that:
1. Validates the source belongs to the current user
2. Enqueues a `generate_from_source` job with the payload
3. Returns `{ job_id }`

New worker handler `generate_from_source` that:
- For `standalone: true` → runs `evaluate_ideas` on just this source, creates episode with overrides
- For `standalone: false` → runs full idea generator pipeline, applies overrides to any episode that contains this `source_id`

---

## Part 4 — Implementation plan

### Phase 1 — Share index (simplest, immediate value)
1. `share/index.tsx`: call `api.addSource(url)` on mount, fire-and-forget
2. `services/api.ts`: add `addSource` method
3. Handle duplicate/error response in the toast UI

### Phase 2 — Remix (self-contained, no new backend endpoint)
1. `remix/[id].tsx`: replace `MOCK_SHOWS` with `api.episode(id)` fetch on mount
2. Add `FORMAT_TO_BACKEND`, `parseDuration`, `snapDuration` helpers
3. Wire "Remix Show" to `api.createEpisode(...)` with mapped params
4. Add loading state on button
5. Navigate to `now-playing/[newId]` on success
6. `services/api.ts`: add `createEpisode` method

### Phase 3 — Share customize (requires new backend endpoint)
1. Backend: `POST /generate-from-source` endpoint + `generate_from_source` job handler
2. Backend: standalone path (evaluate single source → create episode with overrides)
3. Backend: cluster path (full pipeline → apply overrides to matching episodes)
4. Frontend: `share/customize.tsx` wire "Queue It" to `api.generateFromSource(...)`
5. `services/api.ts`: add `generateFromSource` method

### Phase ordering rationale
- Phase 1 is one line of code with immediate impact — sources actually get saved
- Phase 2 is fully self-contained in the frontend, no backend changes needed
- Phase 3 requires a new backend endpoint + job handler — more work, do last

---

## Part 5 — Clustering Optimisations

### 5.1 The problem: cosine scores recomputed every run

Every time `cluster_sources` runs, it recomputes cosine similarity for **every pair** of sources from scratch. The embeddings are already persisted in `source_primitive_embedding` (written once during ingest), but the pairwise scores are not stored anywhere — they live only in the in-memory `scores` dict for the duration of that single run.

With N sources in the pile, each run computes N×(N-1)/2 cosine scores:

```
10 sources  →   45 pairs computed every run
50 sources  → 1225 pairs computed every run
100 sources → 4950 pairs computed every run
```

When a user adds one new bookmark and the pipeline runs, it recomputes `cosine(A,B)` even though A and B haven't changed.

---

### 5.2 The fix: cache pairwise scores in a `source_similarity` table

Store computed scores in the DB the first time they are calculated. On subsequent runs, read from the cache instead of recomputing.

**Schema (new migration needed):**

```sql
CREATE TABLE source_similarity (
    source_a   UUID NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    source_b   UUID NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    score      FLOAT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_a, source_b)
);

CREATE INDEX source_similarity_b_idx ON source_similarity (source_b, source_a);
```

`ON DELETE CASCADE` — when a source is deleted, its similarity rows are automatically cleaned up.
The reverse index covers lookups in both `(A,B)` and `(B,A)` directions.

**Updated `cluster_sources` logic:**

```
For each pair (A, B):
  → check source_similarity(A, B) in DB
  → if found: use cached score
  → if not found: compute cosine(A, B), write to source_similarity, use score
```

**What this means per run:**

```
Pile has A, B, C, D, E — all 10 pairs already cached
User adds F

cluster_sources runs:
  → (A,B), (A,C), (A,D), (A,E), (B,C), (B,D), (B,E), (C,D), (C,E), (D,E) — read from cache
  → (A,F), (B,F), (C,F), (D,F), (E,F) — 5 new computes + writes to cache

Cost per new source = N-1 cosine computations, not N×(N-1)/2
```

At 50 sources: 49 computes instead of 1,225. At 100 sources: 99 instead of 4,950.

---

### 5.3 Also fix: embedding fetches are N round trips, should be 1

The current code fetches embeddings one source at a time:

```python
# Current — N round trips
for source in sources:
    result = await db_query(
        f"SELECT {emb_col} FROM source_primitive_embedding WHERE source_id = $sid::uuid",
        {"sid": bare_sid},
    )
```

This should be a single batched query:

```python
# Fixed — 1 round trip
rows = await db_query(
    f"SELECT source_id, {emb_col} FROM source_primitive_embedding "
    f"WHERE source_id = ANY($ids::uuid[])",
    {"ids": bare_ids},
)
source_embeddings = {str(row["source_id"]): list(row[emb_col]) for row in rows}
```

With the similarity cache in place, this batch fetch is only needed for sources whose pairs are not already cached — typically just the new source. So the actual embedding fetches shrink further.

---

### 5.4 Implementation plan for caching

| Step | Change | File |
|------|--------|------|
| 1 | New migration: `source_similarity` table + indexes | `alembic/versions/` |
| 2 | Batch embedding fetch (single query) | `intelligence/idea_generator.py` |
| 3 | Cache-aware pairwise score computation | `intelligence/idea_generator.py` |
| 4 | Bulk-insert new scores after each run | `intelligence/idea_generator.py` |

**When to implement:** Not urgent at small user counts (< 20 sources per user, recomputation takes milliseconds). Becomes meaningful at 50+ sources per user or when multiple users run the pipeline concurrently.
