# Curia Daily Brief Harness — Dashboard Complete Reference

This document covers the full working of the dashboard: layout, frontend state, every API call, pipeline step logic, prompt templates, rendering, feedback system, history explorer, and the production batch runner.

---

## Architecture Overview

The harness is a FastAPI app (`app/main.py`) that serves two distinct functions:

1. **Test Harness UI** — a single-page vanilla JS dashboard at `/` for manually stepping through the pipeline, inspecting outputs, and giving feedback. Storage is Firestore (prod) or local JSON files (dev).
2. **Production Batch Runner** — a `POST /internal/run-brief-batch` endpoint triggered by Railway cron at 5am UTC, which runs the full pipeline for every brief-enabled user and writes results to Postgres.

The dashboard is `static/index.html` + `static/app.js` + `static/styles.css`, served via FastAPI's `StaticFiles` mount.

---

## Dashboard Layout

### Two Tabs

**Tab 1: Pipeline Workspace** — 3-column grid:

- **Left panel** — User Context Configuration
- **Center panel** — Pipeline Step-by-Step Workspace (Steps 2–6)
- **Right panel** — Spoken Script Preview

**Tab 2: Runs History Explorer** — 2-column grid:

- **Left** — list of all saved cloud/local runs
- **Right** — full detail view of a selected run

**Settings Modal** (gear icon, top-right) — Anthropic API key input, stored in `localStorage`.

---

## Frontend State

All state lives in a single `state` object in `app.js`:

```js
state = {
  interests: [], // active topic chips
  location: "Bangalore",
  saves: [], // JSON array of {title, url, summary}
  daysSinceLastBrief: 2,
  unlistenedQueueCount: 1,
  structure: "Structure 1",
  voice: "Voice A",
  currentRunFilename: null, // filename of the most recently saved/loaded run
  defaultPrompts: {
    // loaded from /api/prompts/defaults on init
    score,
    curate,
    outline,
    transcript,
  },
  stepOutputs: {
    2: null, // array of raw articles
    3: null, // array of scored articles
    4: null, // array of curated selections
    5: null, // array of outline segments
    6: null, // full transcript response object
  },
  articleFeedbacks: {}, // keyed by URL: {title, relevant: bool|null, reason}
};
```

---

## Initialization Sequence

On `DOMContentLoaded`, `init()` runs sequentially:

1. `lucide.createIcons()` — renders all icon SVGs
2. Sync `state.interests` from the active chips in the HTML (default: AI & ML, Semiconductors, Startups & VC, India Tech & Startups)
3. Set saves textarea to `defaultMockSaves` JSON
4. Load Anthropic API key from `localStorage` into the settings modal input
5. `GET /api/status` — checks backend health and API key status; updates status badge color + text
6. `GET /api/prompts/defaults` — loads all 4 system prompts into the editable textareas
7. `GET /api/runs/history` — loads run history into the left-panel collapsible history list
8. `setupListeners()` — binds all event handlers

### API Key Handling

The API key is stored in `localStorage` as `anthropic_api_key`. On every pipeline request, `getHeaders()` checks localStorage and injects it as `X-Anthropic-API-Key` header. The server-side `llm_service.py` uses this header to override the server's default key. If neither exists, the pipeline runs in **Simulated Mode** (mock responses, no real LLM calls).

Status badge states:

- Green glow — `Claude API Online (claude-haiku-4-5-20251001)`
- Violet glow — `Claude Simulated Mode (No API Key)`
- Neutral — `Backend offline`

---

## Left Panel — User Context Configuration

| Field                 | Element                      | Default                                                   |
| --------------------- | ---------------------------- | --------------------------------------------------------- |
| Interest Topic Chips  | Toggle buttons (11 topics)   | AI & ML, Semiconductors, Startups & VC, India Tech active |
| User Name             | Text input `#input-username` | `Aditya`                                                  |
| Home Location         | Text input `#input-location` | `Bangalore`                                               |
| Mock Saved Links      | JSON textarea `#input-saves` | 4 default mock saves (see below)                          |
| Days Since Last Brief | Number input `#input-days`   | `2`                                                       |
| Queue Count           | Number input `#input-queue`  | `1`                                                       |
| Brief Structure       | Radio cards                  | Structure 1 (The Preview)                                 |
| Voice Tone            | Radio cards                  | Voice A (The Smart Friend)                                |

### Available Interest Topics

`AI & Machine Learning`, `Semiconductors & Hardware`, `Startups & Venture Capital`, `Tech Giants (Big Tech)`, `Cybersecurity & Privacy`, `Science & Space`, `Business & Economy`, `India Tech & Startups`, `World News`, `Politics`, `Sports`

### Default Mock Saved Links

```json
[
  {
    "title": "Building AI Agents with Stateful Tools - A Guide",
    "url": "https://example.com/ai-agents-stateful-tools",
    "summary": "An in-depth article describing how function-calling can be made stateful in LLMs to avoid repeating chat history on every invocation."
  },
  {
    "title": "India's Chip Design Industry: Roadmap to 2030",
    "url": "https://example.com/india-chip-design-roadmap",
    "summary": "A policy review of India's semiconductor strategy, emphasizing ATMP facilities as a faster route to market."
  },
  {
    "title": "Deep Dive: How Multi-Agent Coordination Works",
    "url": "https://example.com/multi-agent-coordination",
    "summary": "Explains consensus mechanisms and memory synchronization patterns for complex multi-agent teams using LangGraph."
  },
  {
    "title": "Why Zepto's Private-Label Grocery Bet is Disrupting Quick Commerce",
    "url": "https://example.com/zepto-grocery-brand-disruption",
    "summary": "Discusses how Zepto is expanding into private-label items to expand profit margins beyond logistics delivery."
  }
]
```

### Queue Simulation

`unlistened_queue` is built from the count input:

- Count ≥ 1 → `["The EU AI Act Deep Dive"]`
- Count ≥ 2 → adds `"Sovereign Compute & Chiplets"`
- Count ≥ 3 → adds `"Future of Agent SaaS"`

### `getActiveProfile()` — Profile Assembly

Called before every API request. Reads all left-panel controls and returns:

```js
{
  (interests, // from active chips
    name, // from #input-username
    location, // from #input-location
    saves, // parsed JSON from textarea
    days_since_last_brief,
    unlistened_queue, // assembled from count
    structure, // "Structure 1" or "Structure 2"
    voice); // "Voice A" or "Voice C"
}
```

If JSON parse of the saves textarea fails, falls back to `state.saves`.

---

## Center Panel — Pipeline Steps

Each step card has:

- Status indicator: `Idle` → `Running...` → `Success` / `Error` (with CSS pulse animation on Running)
- Metric badge: article count (Step 2) or latency in ms (Steps 3–6)
- Re-run button and "Run From Here" cascade button (disabled until prior step completes)
- Editable system prompt textarea with a reset-to-default button
- Collapsible output section (auto-expands on success)
- Thumbs up/down + comment feedback widget (shown only on success)

### Step Card Status Classes

| Status  | Card class | Indicator class | Indicator text |
| ------- | ---------- | --------------- | -------------- |
| Idle    | `active`   | `idle`          | `Idle`         |
| Running | `running`  | `running`       | `Running...`   |
| Success | `success`  | `success`       | `Success`      |
| Error   | `error`    | `error`         | `Error`        |

Feedback widget is shown (`display: flex`) on success and hidden on error/idle.

---

## Pipeline Steps — Detailed

### Step 2: Fetch Articles

**Button:** "Fetch Articles"  
**API:** `POST /api/news/fetch`  
**Request body:** full user profile (from `getActiveProfile()`)

**Backend — `news_service.fetch_articles_for_profile()`:**

Fetches from Google News RSS XML via direct `urllib.request` (no gnews library). Three passes:

1. **Interest feeds** (25 articles each):
   - Topic feeds (section/topic URL): `Science & Space`, `Business & Economy`, `World News`, `Politics`, `Sports`
   - Search query feeds (section/search URL): `AI & ML`, `Semiconductors`, `Startups & VC`, `Big Tech`, `Cybersecurity`, `India Tech & Startups`
   - Global topics forced to `gl=US, hl=en-US, ceid=US:en`
   - India/regional topics use `gl=IN, hl=en-IN, ceid=IN:en`

2. **Local geo feed** (15 articles): `https://news.google.com/rss/headlines/section/geo/{location}` — if location is set

3. **Discovery slot** (5 articles): random topic from `[SCIENCE, ENVIRONMENT, SPACE, HEALTH]` not already in the interest list, always at US geolocation

Deduplication is by URL (exact match) and normalized title (alphanumeric lowercase). HTML tags, tracking links, and anchor tags are stripped from descriptions. Source name is extracted from `-` suffix in title.

**Fallback:** If fewer than 4 articles total, 4 hardcoded mock articles are appended (Anthropic $65B raise, Claude Opus 4.8, Google Hyderabad data center, Zepto $350M round).

**Response:** `{ status, articles_count, articles: [{title, description, source, url, published_date, topic}] }`

**Frontend rendering:**

- Category filter pills rendered above the list (All + each unique topic with count)
- Per-article card shows: title (clickable link), description (truncated to 140 chars), source/topic/date metadata, external link arrow button
- Each article has an inline **Relevance Widget**: Yes/No toggle buttons + free-text reason input
- Article feedback is saved to `state.articleFeedbacks[url]` on click/type
- Step 3 and cascade buttons are enabled after success
- Article feedback state is reset on each new Step 2 run

---

### Step 3: Score & Rank (LLM)

**Button:** "Re-Run Step 3" / "Run From Here" (cascade 3→4→5→6)  
**API:** `POST /api/pipeline/score`  
**Request body:**

```json
{
  "user_profile": { ...profile },
  "articles": [ ...step2Output ],
  "prompt_override": "...edited prompt or null if unchanged"
}
```

**Backend — `pipeline_manager.run_score_step()`:**
Formats saves and articles to strings, fills `USER_SCORE_PROMPT`, calls `llm_service.call_llm()` with `step_id=3`, `temperature=0.2`.

**Model:** `claude-haiku-4-5-20251001`

**System Prompt (`SYSTEM_SCORE_PROMPT`):**

```
You are an elite, hyper-personalized news filtering engine. Score every article on 4 dimensions from 1.0 to 10.0:
1. Topic Alignment: How well does this story align with the user's active interest chips?
2. Recency: Is this breaking or highly fresh information, or is it older/rehashed?
3. Significance: Overall global or regional impact of the story.
4. Save Resonance: How strongly does this story connect with the user's recently saved links?

Composite = (TopicAlignment * 0.40) + (SaveResonance * 0.35) + (Significance * 0.15) + (Recency * 0.10)

Response MUST be valid JSON:
{
  "scored_articles": [
    {
      "title": "...",
      "scores": { "topicAlignment": 8.5, "recency": 9.0, "significance": 8.0, "saveResonance": 7.5 },
      "composite": 8.05
    }
  ]
}
```

**User Prompt (`USER_SCORE_PROMPT`):**

```
User profile:
- Interests: {interests}
- Location: {location}
- Mock Saved Links:
{saves}

Raw article pool:
{articles}

Score each article and return the JSON.
```

**Response parsed:** `data.parsed.scored_articles` → stored in `state.stepOutputs[3]`

**Frontend rendering:** Scored articles table with columns: Title (50 chars), Align, Recency, Signif, Resonance, COMPOSITE (bold, highlighted).

**Simulated mode response (no API key):**
Returns 5 pre-scored mock articles with composite scores ranging 4.9–9.35.

---

### Step 4: Editorial Curation (LLM)

**Button:** "Re-Run Step 4" / "Run From Here" (cascade 4→5→6)  
**API:** `POST /api/pipeline/curate`  
**Request body:**

```json
{
  "user_profile": { ...profile },
  "scored_articles": [ ...step3Output ],
  "prompt_override": null
}
```

**Backend — `pipeline_manager.run_curate_step()`:**
Formats scored articles to JSON string, fills `USER_CURATE_PROMPT`, calls LLM with `step_id=4`, `temperature=0.2`.

**System Prompt (`SYSTEM_CURATE_PROMPT`):**

```
You are the Senior Executive Producer of the Curia Daily Brief. Curate a 5-8 minute daily news broadcast.

Strict slot budget:
- Lead Story: 1 slot (highest relevance, largest impact)
- Standard Stories: 2-3 slots (high-quality headlines across user's interests)
- Local News: 1 slot (anchored to user's location)
- Discovery Slot: 1 slot (deliberately outside standard interests to break filter bubble)
- All remaining: "cuts" with explicit editorial reason

Response MUST be valid JSON:
{
  "selections": [
    { "title": "...", "slot": "Lead Story|Standard 1|Standard 2|Standard 3|Local|Discovery", "reason": "..." }
  ],
  "cuts": [
    { "title": "...", "reason": "..." }
  ]
}
```

**Response parsed:** `data.parsed.selections` → `state.stepOutputs[4]`

**Frontend rendering:**

- Each selection rendered as a slot card with badge color:
  - Lead Story → accent color
  - Discovery → discovery color
  - Local → local color
  - Standard N → neutral
- Cuts shown below with strikethrough title, 50% opacity, CLIPPED badge in red

---

### Step 5: Narrative Outline (LLM)

**Button:** "Re-Run Step 5" / "Run From Here" (cascade 5→6)  
**API:** `POST /api/pipeline/outline`  
**Request body:**

```json
{
  "user_profile": { ...profile },
  "selections": [ ...step4Output ],
  "prompt_override": null
}
```

**System Prompt (`SYSTEM_OUTLINE_PROMPT`):**

```
You are a master Narrative Designer. Create a detailed segment-by-segment blueprint for a 5-8 minute audio daily brief.

Thread together:
1. Personal User Activity (saved links, unlistened episodes — acknowledge warmly, not pushily)
2. Curated News Stories (logical transitions between stories)
3. Local context (location news and weather)
4. Discovery item
5. Outro (episode recommendation from unlistened queue)

Word budgets must sum to roughly 650-800 words (~5-6 minutes of speech).

Response MUST be valid JSON:
{
  "segments": [
    {
      "type": "INTRO|ACTIVITY|NEWS_GLIMPSE|NEWS_LEAD|NEWS_STANDARD_1|NEWS_STANDARD_2|NEWS_STANDARD_3|LOCAL_WEATHER|CLOSING_THOUGHT|OUTRO",
      "content_plan": "Specific details on what will be spoken, transitions, and personal ties.",
      "word_budget": 50
    }
  ]
}
```

**User Prompt (`USER_OUTLINE_PROMPT`):**

```
User profile and recent activity:
- Interests: {interests}
- Location: {location}
- Active Saves Count: {saves_count}
- Saves list: {saves}
- Days since last brief: {days_since_last_brief}
- Unlistened episode queue: {unlistened_queue}

Selected articles for today's slots:
{selections}
```

**Response parsed:** `data.parsed.segments` → `state.stepOutputs[5]`

**Frontend rendering:** Each segment shown as a row with: type tag (monospace uppercase), content_plan text, word budget.

---

### Step 6: Conversational Transcript (LLM)

**Button:** "Write Spoken Transcript"  
**API:** `POST /api/pipeline/transcript`  
**Request body:**

```json
{
  "user_profile": { ...profile },
  "outline": [ ...step5Output ],
  "selections": [ ...step4Output ],
  "prompt_override": null
}
```

**Model:** `claude-haiku-4-5-20251001`, `temperature=0.7`

**System Prompt (`SYSTEM_TRANSCRIPT_PROMPT`):**

Structure options:

- **Option 1 — "The Preview":** Intro → Activity → News Glimpse (Teaser) → News Lead → Standard News (1-3) → Local News & Weather → Closing Thought/Discovery → Outro. Character: highly structured, produced, table-of-contents preview upfront.
- **Option 2 — "The Lead":** Intro → Lead Story (immediate hook) → Activity (breather) → Standard News (1-3) → Local News & Weather → Outro (discovery folded into sign-off). Character: punchy, momentum-first, zero-preview hook.

Voice options:

- **Voice A — "The Smart Friend":** Casual-conversational. Uses contractions, short sentences. Direct, warm, occasionally dry. Informed but peer-to-peer. First-person ('I found this interesting') and second-person ('You saved this'). Strictly avoids: radio anchor cadence, corporate filler ('It is worth noting', 'Moving on', 'Next up'), empty transition statements.
- **Voice C — "The Curious Companion":** Genuinely enthusiastic, conversational, slightly playful. Thinks out loud. Focuses on the fascinating angle or systemic connection of a story. Peer-like energy, asks questions. Strictly avoids: podcast-bro hyperbole, detached neutrality, corporate jargon.

Key transcript rules:

1. Friendly time-aware greeting with username and day/date
2. Activity acknowledgment must be extremely light — never pressure or guilt-trip
3. Every news story must have: what happened + why it matters + user connection (only if genuine)
4. Fold local weather into Intro (Structure 2) or Local News segment (Structure 1)
5. No news broadcaster clichés — smooth thematic transitions
6. Wrap up with tease of unlistened episode from queue
7. Every segment wrapped in bracket header: `[INTRO]`, `[ACTIVITY]`, `[NEWS GLIMPSE]`, etc.

**User Prompt (`USER_TRANSCRIPT_PROMPT`):**

```
Configuration:
- User's Name: {username}
- Chosen Structure: {structure}
- Chosen Voice: {voice}
- User Profile: Location {location}, Interests {interests}
- Saved links: {saves}
- Queue: {unlistened_queue}

Narrative outline:
{outline}

Selected articles:
{articles_content}
```

**Post-processing:** Backend parses bracket headers via regex `\[([^\]]+)\](.*?)(?=\[[^\]]+\]|$)` into `parsed_segments: [{type, text, word_count}]`. Falls back to a single `FULL TRANSCRIPT` segment if no brackets found.

**Response stored:** full response object in `state.stepOutputs[6]` including `text`, `parsed_segments`, `latency_ms`, `simulated`.

**Frontend rendering (Right Panel):**

- Each parsed segment rendered as a `transcript-flow-block` with color-coded left border by type:
  - `intro` → intro style
  - `activity` → activity style
  - `glimpse` → glimpse style
  - `lead` → lead style
  - `local` / `weather` → local style
  - `thought` / `discovery` → thought style
  - `outro` → outro style
- Stats bar updates: total word count, estimated duration at 150 WPM, segment count
- Copy button enabled (copies joined segment text without bracket headers)
- Export button enabled (downloads full pipeline JSON)
- Run auto-saves to history via `saveRunToHistory()`

**Simulated mode responses (no API key):**

Voice A ("Smart Friend") returns a structured `[INTRO]`, `[ACTIVITY]`, `[NEWS GLIMPSE]`, `[NEWS - LEAD]`, `[NEWS - STANDARD 1]`, `[NEWS - STANDARD 2]`, `[NEWS - STANDARD 3]`, `[LOCAL + WEATHER]`, `[CLOSING THOUGHT]`, `[OUTRO]` transcript.

Voice C ("Curious Companion") returns `[INTRO]`, `[LEAD STORY]`, `[ACTIVITY]`, `[NEWS - STANDARD 1-3]`, `[LOCAL + WEATHER]`, `[OUTRO]` format — more enthusiastic, question-driven tone.

---

## Cascade & Run-All Execution

| Action                    | Sequence                                      |
| ------------------------- | --------------------------------------------- |
| "Run End-to-End Pipeline" | Step 2 → 3 → 4 → 5 → 6 (stops on any failure) |
| "Run From Here" on Step 3 | Step 3 → 4 → 5 → 6                            |
| "Run From Here" on Step 4 | Step 4 → 5 → 6                                |
| "Run From Here" on Step 5 | Step 5 → 6                                    |

All cascades are sequential (each `await`s the previous). Any step returning `false` aborts the chain.

During "Run End-to-End", the button shows a pulsing dot and `Executing Pipeline...` and is disabled until completion.

---

## Feedback System

### Step-Level Feedback

Each step card (3–6) shows a feedback widget after a successful run:

- Thumbs up / thumbs down toggle (mutually exclusive)
- Free-text comment input
- Save button → `POST /api/runs/feedback` with `{filename, step_id, rating, comment}`
- On success: button flashes green "Saved!" for 2 seconds, then resets
- History sidebar list is refreshed after each save to update thumbs counts

Feedback requires an active `state.currentRunFilename`. If none (no run saved yet), shows an alert.

### Article-Level Feedback

Per-article in Step 2 output:

- Yes / No toggle buttons (in-memory state update on click)
- Reason text input (in-memory state update on input, no debounce in current impl)
- State stored in `state.articleFeedbacks[url]`
- Persisted to the run document when `saveRunToHistory()` is called after Step 6

---

## Run History Management

### Auto-Save After Step 6

`saveRunToHistory()` is called automatically at the end of Step 6. Payload:

```json
{
  "user_profile": { ...profile },
  "pipeline_steps": {
    "step_2_raw_articles": [...],
    "step_3_scored_articles": [...],
    "step_4_curated_slots": [...],
    "step_5_narrative_outline": [...],
    "step_6_transcript": { text, parsed_segments, latency_ms, simulated }
  },
  "article_feedbacks": { url: {title, relevant, reason} }
}
```

API: `POST /api/runs/save` → returns `{status, filename}`. `state.currentRunFilename` is set to the returned filename.

### Left Panel — History List

Collapsible "Saved Runs History" in the left panel. Loaded on init and after every feedback save.

Each history item shows:

- Timestamp (month-day + time, year stripped)
- Word count
- Location, voice, structure badges
- Thumbs up/down counts (if any)

Clicking an item calls `loadPastRunDetails(filename)` which:

1. `GET /api/runs/details/{filename}`
2. Restores all UI controls (chips, inputs, radios, saves textarea)
3. Restores `state.stepOutputs[2-6]` and `state.articleFeedbacks`
4. Re-renders all step outputs and sets all card statuses to success
5. Restores feedback widget states (thumbs + comments)
6. Sets `state.currentRunFilename` to the loaded file
7. Auto-collapses history, auto-expands Step 6 collapsible

### History Explorer Tab

Separate full-page explorer view. Loaded when switching to the History tab.

**Left panel** — run list cards, each showing: full timestamp, word count, location/voice/structure badges, interests list, thumbs summary. Clicking selects the run and highlights it.

**Right panel** — full run detail in 2-column layout:

- Left column:
  - Article Relevance Reviews (color-coded by relevant/not/unrated, with reason)
  - Step 3 scoring table (all articles, all scores + composite)
  - Step 4 editorial slots (badge + title + editorial reason)
  - Step 5 narrative outline (type | content_plan | word budget)
- Right column:
  - Step-by-step feedback log (per-step thumbs + comment)
  - Step 6 transcript preview (scrollable, segment by segment)

**"Load into Workspace" button** — calls `loadPastRunDetails()` and switches back to workspace tab.

---

## Export & Utilities

### Copy Transcript

Joins `parsed_segments[].text` with double newlines (bracket headers stripped), copies to clipboard via `navigator.clipboard.writeText()`.

### Export Pipeline JSON

Downloads a JSON file named `daily_brief_pipeline_export_{timestamp}.json` containing:

```json
{
  "timestamp": "ISO string",
  "user_profile": { ... },
  "pipeline_steps": {
    "step_2_raw_articles": [...],
    "step_3_scored_articles": [...],
    "step_4_curated_slots": [...],
    "step_5_narrative_outline": [...],
    "step_6_transcript": { ... }
  }
}
```

---

## Backend Storage — `history_service.py`

Dual-mode: Firestore (if `K_SERVICE` env var set, i.e., Cloud Run, or `USE_FIRESTORE=true`) or local JSON files at `app/data/runs/run_{timestamp_ms}.json`.

Also supports `GOOGLE_CREDENTIALS_JSON` env var (JSON string) for non-GCP platforms like Render.

Operations:

- `save_run(run_data)` → writes full run payload, returns filename
- `list_runs()` → returns summary list (filename, timestamp, interests, location, voice, structure, word_count, upvotes, downvotes)
- `get_run(filename)` → returns full run dict
- `save_feedback(filename, step_id, rating, comment)` → patches `feedback.{step_id}` in the document
- `save_article_feedback(...)` → **NOT IMPLEMENTED** (method missing, will throw AttributeError)

---

## Production Batch Runner

### Trigger

`POST /internal/run-brief-batch` — protected by `X-Internal-Key` header matching `INTERNAL_SECRET` env var. Called by Railway cron at 5am UTC.

### Flow

1. `GET {CURIA_BACKEND_URL}/internal/brief-context/all` — fetches all brief-enabled users. Expected shape per user: `{user_id, name, interests, location_city, recent_saves: [{title, url, description}], fcm_token}`
2. `run_batch(users, today)` — runs `generate_brief_for_user` for each user concurrently, capped at 5 simultaneous via `asyncio.Semaphore(5)`, 600s timeout per user
3. Returns `{processed, failed, total}`

### `generate_brief_for_user(user, brief_date)`

Full pipeline per user:

1. `db_service.brief_set_generating(user_id, date)` — idempotent INSERT into `daily_briefs` table (skips if row exists for that user+date)
2. `news_service.fetch_articles_for_profile()` — takes first 40 articles
3. `pipeline.run_score_step()` → `pipeline.run_curate_step()` → `pipeline.run_outline_step()` → `pipeline.run_transcript_step()`
4. `tts_service.synthesize_text(transcript_text, voice_id=BRIEF_VOICE_ID)` — Smallest.ai Lightning API, 200-char chunks, stitched into WAV
5. `storage_service.upload_audio(audio_bytes, key=briefs/{user_id}/{date}.wav)` — S3/R2 upload
6. `db_service.brief_set_ready(...)` — writes transcript, audio_url, audio_duration_seconds, articles, outline to `daily_briefs` row
7. `push_service.send_brief_ready(fcm_token, date)` — FCM notification: title "Your morning brief is ready", body "Tap to listen to today's news", data `{type: daily_brief, date}`
8. `db_service.brief_mark_pn_sent(user_id, date)`

On any exception: `db_service.brief_set_failed(user_id, date, error_text)` is called.

### `daily_briefs` Table Schema (required, not yet migrated)

```sql
daily_briefs (
  user_id              TEXT,
  date                 DATE,
  status               TEXT,  -- 'generating' | 'ready' | 'failed'
  transcript           TEXT,
  audio_url            TEXT,
  audio_duration_seconds FLOAT,
  articles_json        JSONB,
  outline_json         JSONB,
  error_text           TEXT,
  pn_sent              BOOLEAN DEFAULT FALSE,
  updated_at           TIMESTAMPTZ,
  PRIMARY KEY (user_id, date)
)
```

---

## TTS — `tts_service.py`

Provider: **Smallest.ai Lightning** (`https://waves-api.smallest.ai/api/v1/lightning/get_speech`)  
Sample rate: 24000 Hz, mono, 16-bit PCM, stitched into WAV.

Chunking: text is split at sentence boundaries (`[.!?]\s+`), then by word boundaries if any sentence exceeds 200 chars. Bracket segment headers (`[INTRO]`, etc.) are stripped before chunking.

Each chunk is synthesized sequentially with a 60s timeout. On failure or non-200 response, 1 second of silence (zeroed PCM) is inserted for that chunk. Final PCM chunks are concatenated into a single WAV via `wave` module.

---

## LLM Configuration — `llm_service.py`

Model: `claude-haiku-4-5-20251001`  
Temperature: `0.2` for steps 3, 4, 5 (structured JSON); `0.7` for step 6 (creative transcript)  
Max tokens: `4000`

JSON response cleaning: strips leading/trailing ` ```json ` and ` ``` ` code fences before `json.loads()`.

---

## Required Environment Variables

| Variable                        | Used by                              | Notes                                         |
| ------------------------------- | ------------------------------------ | --------------------------------------------- |
| `ANTHROPIC_API_KEY`             | `llm_service.py`                     | Falls back to simulated mode if absent        |
| `DATABASE_URL`                  | `db_service.py`                      | asyncpg connection string                     |
| `SMALLEST_API_KEY`              | `tts_service.py`                     | Required for audio generation                 |
| `BRIEF_VOICE_ID`                | `tts_service.py` + `brief_runner.py` | e.g. `emily`                                  |
| `CURIA_S3_BUCKET`               | `storage_service.py`                 | R2 or S3 bucket name                          |
| `CURIA_S3_ENDPOINT`             | `storage_service.py`                 | R2 endpoint URL (omit for AWS S3)             |
| `CURIA_S3_ACCESS_KEY`           | `storage_service.py`                 |                                               |
| `CURIA_S3_SECRET_KEY`           | `storage_service.py`                 |                                               |
| `CURIA_S3_REGION`               | `storage_service.py`                 | Default: `auto`                               |
| `CURIA_BACKEND_URL`             | `main.py` batch trigger              | Base URL of curia-backend                     |
| `INTERNAL_SECRET`               | `main.py` batch trigger              | Header auth for `/internal/run-brief-batch`   |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | `push_service.py`                    | JSON string of Firebase SA credentials        |
| `PORT`                          | `config.py`                          | Default: 8000                                 |
| `HOST`                          | `config.py`                          | Default: `127.0.0.1`                          |
| `DEBUG`                         | `config.py`                          | Default: `true`                               |
| `USE_FIRESTORE`                 | `history_service.py`                 | Set to `"true"` to force Firestore on non-GCP |
| `GOOGLE_CREDENTIALS_JSON`       | `history_service.py`                 | Firestore SA JSON string for non-GCP          |

---

## Known Bugs

1. **`tts_service.py` crashes at runtime** — uses `logger` without importing `logging` or defining `logger`. Any TTS call in production will raise `NameError: name 'logger' is not defined`.

2. **`history_service.save_article_feedback` is missing** — `POST /api/runs/article-feedback` routes to this method but it doesn't exist in `history_service.py`. Will throw `AttributeError` every time article feedback is saved from the UI.

3. **Batch runner extracts LLM results from wrong keys** — `llm_service.call_llm` returns `{text, parsed, latency_ms, ...}`. `brief_runner.py` tries `score_result.get("scored_articles")`, `curate_result.get("selections")`, `outline_result.get("outline")` — none of these keys exist at the top level. Should be `score_result["parsed"]["scored_articles"]` etc. With a real API key, all three steps produce empty results and the pipeline generates a transcript with no articles.

4. **`daily_briefs` table doesn't exist** — no Alembic migration has been written for it. The table must be created in curia-backend before the batch runner can write anything to Postgres.

5. **`/internal/brief-context/all` endpoint doesn't exist in curia-backend** — the batch trigger calls this endpoint to get all brief-enabled users. It needs to be built, and a `brief_enabled` column needs to be added to the `users` table to filter who receives a brief.

6. **Score weight mismatch** — spec documents weights `(0.35, 0.15, 0.25, 0.25)` but `SYSTEM_SCORE_PROMPT` instructs the LLM to use `(TopicAlignment * 0.40) + (SaveResonance * 0.35) + (Significance * 0.15) + (Recency * 0.10)`.

7. **`gnews>=0.3.6` in requirements.txt is unused** — news service uses raw `urllib` + `ElementTree`, not the gnews package.
