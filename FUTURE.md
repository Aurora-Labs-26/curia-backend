# Curia — Future Direction

---

## Part 1 — Technical Improvements

### Clustering — find more interesting multi-source episode ideas

Current approach uses cosine similarity on full-text embeddings. Voyage-3 scores 0.80+ across unrelated articles, making threshold-based clustering unreliable. Most sources end up as solos.

#### Options (in order of impact)

**1. LLM-driven clustering**
Replace cosine similarity entirely. Send all source primitives to an LLM and ask it to group sources that could make interesting episodes together. It can see thematic connections, tensions, and counterpoints that embeddings miss.
- More expensive per run
- Produces semantically meaningful clusters
- Most impactful change

**2. Cluster on primitives, not full text**
Embed only `core_tensions` and `counterpoints` per source instead of full article text. Two sources with related tensions could connect well even if topics are different.
- Cheap to implement
- Better signal than full-text embeddings

**3. Tag-based grouping**
Add a domain/theme tag at ingest time (e.g. "ambition", "cognition", "markets", "relationships"). Cluster within tags first, then look for cross-tag combinations with a connecting thread.
- Requires tagging step at ingest
- More predictable and debuggable than embedding similarity

**4. Two-pass clustering**
Keep cosine for rough grouping, then add a second LLM pass on candidate pairs/triples asking: "does a tension or unexpected connection exist between these sources that could sustain a 12-minute episode?" Filter out pairs where the answer is no.
- Builds on existing infrastructure
- LLM pass adds meaningful filter on top of noisy cosine signal

### Other technical improvements

- Idea generator: recommend format based on source primitives at the cluster level, not just standalone
- TTS: add reference WAVs for Arjun and Emeka speakers

---

## Part 2 — Curia as a Companion, Not a Vending Machine

### The thesis

**Curia today is a vending machine.** The user puts URLs in. They press a button. Episodes come out. Between button presses, nothing happens. The system is dormant.

**Curia tomorrow is a producer.** Between sessions it is *thinking about your archive* — re-clustering as new things come in, drafting candidate episodes you didn't ask for, building a model of what you actually listen to vs skip, holding a sense of what's emerging in your reading and what's gone stale. When you open the app, there is already a draft waiting. When you save your fifth article on the same theme, it pings you: *"there's something here, want me to make it?"*

This shift matters because podcast generation from a personal archive is fundamentally a **curatorial taste problem**, not a button-press problem. The product gets dramatically better the more the system understands you. A vending machine cannot understand you; only an agent that runs continuously alongside you can.

---

### Current status

Some of what this doc describes has shipped. Some hasn't.

#### Shipped — the personalization substrate

- **User knowledge bank (Layer 1)** — `core/kb/` with full Pydantic schema. Persisted in `users.user_kb` JSONB. `GET /PUT /me/kb` exposes it.
- **KB → pipeline integration** — selector derives editorial direction from `current_obsession`; briefing builder injects `listener_context`; transcript generation appends listener hints; idea generation filters dislikes and biases toward interests.
- **Rubric judge** — renders a per-user judge prompt from `(company guidelines + user KB)`. Every transcript gets scored; auto re-rolls once if score < threshold. Result stored on `episode.quality_score`.
- **User-visible rubric** — `GET /me/rubric/{task}` returns the rendered judge prompt.
- **QA toolkit** — admin routes for rubric inspection, live guidelines edits, examples CRUD, optimization runs.

#### Not yet shipped — the parts that make it a companion

- **Event log** (`events` table + emitter)
- **Listening telemetry** (`POST /events`, `episode_listens` aggregation)
- **Reflect engine** (idle + event-driven cycles, the agentic loop with tool use)
- **Agent journal** (`agent_journal` table, visible in feed)
- **Notification bus** (`notifications` table, `GET /feed`)
- **Proactive draft generation** (the "draft appears unprompted" wow moment)

#### Phase status

| Phase | What | Status |
|---|---|---|
| (Pre-Phase A) | KB substrate | ✅ shipped |
| Phase A | Events table + emitter | ❌ not started |
| Phase B | Listening telemetry | ❌ not started |
| Phase C | Idle reflection (observe-only) | ❌ not started |
| Phase D | Proactive drafts | ❌ not started |
| Phase E | Event-driven reflection | ❌ not started |
| Phase F | Personality polish | ❌ not started |
| Phase G | Long-term memory consolidation | ❌ not started |
| Phase H | Push channels | ❌ not started |

Phase D is the right alpha demo target — that's where users feel the difference. Phases A–C are foundational scaffolding without which D doesn't work. ~5 weeks of focused work from end of v1 to Phase D demoable.

---

### What the companion looks like to the user

- **Drafts appear unprompted** — every few hours the companion picks the strongest cluster and quietly drafts an episode. You wake up, the draft is there.
- **It learns your taste** — it knows you finish `clarity_engine` episodes but skip `narrative_drift` past minute 4.
- **It notices patterns you don't** — *"Three of your last five saves circle around 'attention as a finite resource' — want an episode on this?"*
- **It writes notes to itself, visible to you** — a small feed shows the companion's own thinking. This transparency is the trust mechanism.
- **It reflects overnight** — prune stale ideas, consolidate listening data, surface a "this week" recap.
- **It has continuity** — when you come back after a week away, it remembers.

---

### Architecture

The infrastructure in BACKEND_PLAN — API, Worker, Postgres+pgvector, queue — is the right base. The companion is a layer on top.

What gets added:

```
companion/
├── reflect.py              ← the agentic loop
├── prompt.py               ← the companion system prompt
├── tools/
│   ├── draft_episode.py
│   ├── update_user_model.py
│   ├── open_thread.py
│   ├── close_thread.py
│   ├── write_journal.py
│   ├── send_nudge.py
│   └── do_nothing.py
└── triggers/
    ├── event_driven.py
    └── scheduled.py

core/events.py              ← emit + query

api/routes/
├── feed.py                 ← agent journal + notifications
├── events.py               ← frontend posts listen events
└── companion.py            ← read-only access to state
```

### New API endpoints

```
POST   /events
GET    /feed
GET    /feed/journal
GET    /feed/notifications
POST   /feed/notifications/:id/read
POST   /feed/notifications/:id/dismiss
GET    /me/companion
GET    /me/companion/threads
POST   /episodes/:id/feedback
POST   /me/companion/reflect
```

### Tool catalogue

| Tool | Purpose |
|---|---|
| `update_user_model` | Refine preferences in companion state |
| `open_thread` | Register an emerging theme |
| `close_thread` | Mark a theme done/stale |
| `draft_episode` | Enqueue the existing generate_episode worker job |
| `write_journal` | Note something for the user to see |
| `send_nudge` | Surface a notification |
| `do_nothing` | Explicit no-op — bias toward silence |

### Cost estimate

- Light cycle (Haiku, every 4h): ~$0.005/cycle
- Deep cycle (Sonnet, daily): ~$0.10/cycle
- 100 active users: ~$400/month before any drafts
- Hard cap recommended: ~$0.20/user/day for v1

### Key decisions

- **Identity model** — start with light personality (name + stable tone). Full character is v3.
- **Push surface** — in-app feed only through Phase E, email digests in Phase H.
- **Alpha demo target** — Phase D (proactive drafts).

### Open questions

- Cold-start: new user has no events — companion has nothing to reflect on for the first week
- The "creepy" line: how much does the companion *show it knows* vs hold back early on
- Failure modes: user gets 5 bad drafts in a row — how do they signal "stop trying that"
- Multi-device telemetry dedup
- Privacy: listening data is intimate, encryption at rest mandatory before alpha
- Shipping cadence: frequency too high feels noisy, too low feels dead

---

### Order of operations

```
1. Ship BACKEND_PLAN v1 alpha      ← the vending machine (good, useful, ships)
2. Phase A (events substrate)       ← invisible foundation
3. Phase B (listening telemetry)    ← invisible foundation
4. Phase C (silent reflection)      ← first signs of life in the journal
5. Phase D (proactive drafts)       ← the "wow" — companion goes live
6. Phases E–G                        ← deepens the experience
7. Phase H                           ← pushes outside the app
```

The companion is layered, not entangled. Pull the reflect worker, the system reverts to the vending machine. No data loss, no rollback.
