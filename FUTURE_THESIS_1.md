# Future Thesis 1 — Curia as a Companion, Not a Vending Machine

This doc proposes a directional shift for Curia, layered on top of the architecture in [BACKEND_PLAN.md](BACKEND_PLAN.md). Originally written before Tier 1 personalization shipped — read the *current status* section below first to see what's actually built vs still aspirational.

---

## Current status (updated as code lands)

Some of what this doc describes has shipped. Some hasn't. Here's the honest map:

### ✅ Shipped — the personalization substrate

These are running in the codebase today and are exercised by every episode:

- **User knowledge bank (Layer 1)** — `core/kb/` with full Pydantic schema (`identity`, `interests`, `preferences`, `listening_context`, `dislikes`). Persisted in `users.user_kb` JSONB. `scripts/onboard.py` populates it; `GET / PUT /me/kb` exposes it.
- **KB → pipeline integration** — selector derives editorial direction from `current_obsession`; briefing builder injects `listener_context` block; transcript generation appends listener hints to the speaker definition; idea generation filters dislikes and biases toward interests.
- **Rubric judge (Layer 1 of curatorial state)** — `optimization/rubrics/` renders a per-user judge prompt from `(company guidelines + user KB)`. Every transcript gets scored; auto re-rolls once if score < `CURIA_QUALITY_THRESHOLD`. Result stored on `episode.quality_score`/`quality_feedback`/`quality_violations`/`regenerated`.
- **User-visible rubric** — `GET /me/rubric/{task}` returns the rendered judge prompt — the transparency mechanism described in §2.4.
- **QA toolkit** — `/admin/users/{id}/rubric/{task}` (any user's rubric for inspection), `/admin/guidelines` PUT (live-edits propagate to the rubric immediately), `/admin/examples` CRUD, `/admin/optimization/runs` (kick off + promote GEPA artifacts).

### ❌ Not yet shipped — the parts that make it a *companion*

The core pivot — from request/response to always-on producer — is still entirely planned:

- **Event log** (`events` table + emitter) — §3.1
- **Listening telemetry** (`POST /events`, `episode_listens` aggregation) — §3.1
- **Reflect engine** (idle + event-driven cycles, the agentic loop with tool use) — §3.3
- **Agent journal** (`agent_journal` table, the companion's notes-to-self visible in feed) — §3.2
- **Notification bus** (`notifications` table, `GET /feed`) — §3.4
- **Proactive draft generation** (the "draft appears unprompted" wow moment) — §2.1, §10 Phase D

### What's the difference today?

Without the companion, Curia is still **"reactive but personalized"**: you press a button, an episode is shaped to your KB, the judge scores it against your rubric, you listen.

The thesis below describes turning that into **"proactive and reflective"**: between sessions Curia is *thinking about your archive*, drafting candidates, noticing patterns, writing journal notes, surfacing nudges. None of that loop runs yet.

### Where we are on the phased path (§10)

| Phase | What | Status |
|---|---|---|
| (Pre-Phase A) | KB substrate | ✅ shipped (out-of-order — the user-shaped half landed before the agent-shaped half) |
| Phase A | Events table + emitter | ❌ not started |
| Phase B | Listening telemetry | ❌ not started |
| Phase C | Idle reflection (observe-only) | ❌ not started |
| Phase D | Proactive drafts | ❌ not started |
| Phase E | Event-driven reflection | ❌ not started |
| Phase F | Personality polish | ❌ not started |
| Phase G | Long-term memory consolidation | ❌ not started |
| Phase H | Push channels | ❌ not started |

So the architecture below is still the right north star — what changed is that the *personalization layer* (Layer 1 → Layer 4) shipped before the *agentic loop layer* did. The full companion experience needs Phase A → D at minimum, ~13 hours of focused work.

---

---

## 1. The thesis

**Curia today is a vending machine.** The user puts URLs in. They press a button. Episodes come out. Between button presses, nothing happens. The system is dormant.

**Curia tomorrow is a producer.** Between sessions it is *thinking about your archive* — re-clustering as new things come in, drafting candidate episodes you didn't ask for, building a model of what you actually listen to vs skip, holding a sense of what's emerging in your reading and what's gone stale. When you open the app, there is already a draft waiting. When you save your fifth article on the same theme, it pings you: *"there's something here, want me to make it?"*

This shift matters because podcast generation from a personal archive is fundamentally a **curatorial taste problem**, not a button-press problem. The product gets dramatically better the more the system understands you. A vending machine cannot understand you; only an agent that runs continuously alongside you can.

The architectural ingredients for this kind of system — long-running background processes, persistent per-user memory, idle-time consolidation, agentic loops with tool use — are exactly the same ingredients that AI products generally are moving toward. Curia is a good place to do this because the loop is tight: *you save something → it produces something → you listen → it learns*. The feedback signal is rich and continuous.

---

## 2. What this looks like to the user

Concrete behaviors a companion Curia exhibits — the things an alpha user would notice within a week:

### 2.1 Drafts appear unprompted
Every few hours the companion picks the strongest cluster in your archive and quietly drafts an episode. You wake up, the draft is there. You can listen, regenerate, or dismiss. Dismissals feed back into its model.

### 2.2 It learns your taste
It knows you finish `clarity_engine` episodes but skip `narrative_drift` past minute 4. It notices you open Curia at 7:45am on weekdays and listen on a commute. Future drafts weight toward what you actually consume.

### 2.3 It notices patterns you don't
*"Three of your last five saves circle around 'attention as a finite resource' — different angles. Want an episode on this?"* Surfaced in-app, dismissable.

### 2.4 It writes notes to itself, visible to you
A small feed shows the companion's own thinking:
> *Tuesday — User saved 2 articles on memory; both reference the same study. Holding for a third before clustering.*
> *Wednesday — Started episode on remote work, didn't finish. Marking that thread cool.*

This transparency is the trust mechanism. The companion is not a black box — you can read what it's been thinking.

### 2.5 It reflects overnight
Once a day, a longer pass: prune stale ideas, consolidate listening data into a refined user model, surface a "this week" recap. Daily/weekly digests are a natural side-effect of consolidation.

### 2.6 It has continuity
When you come back after a week away, it remembers. *"You haven't generated an episode since last Sunday. The cluster on remote work is still warm — want to revisit?"* It does not start over.

### 2.7 (Optional) It has identity
A name. A consistent voice in its journal. Maybe a backstory. This is the BUDDY-shaped territory — *not* required for the system to work, but it's how a companion stops feeling like UI and starts feeling like *someone*. We treat this as a polish layer in Phase F, not a foundation.

---

## 3. Architectural shifts

The infrastructure in BACKEND_PLAN — API, Worker, Postgres+pgvector, queue — is **the right base**. It does not get thrown out. The companion is a *layer* on top of it.

What gets added:

```
                    ┌──────────────────────────────────┐
                    │   API   ←→   Worker (jobs)        │   ← what we already planned
                    │              │                    │
                    │              └──→ uses Claude     │
                    └──────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┬───────────────┐
              ▼               ▼               ▼               ▼
        ╔═══════════╗   ╔═══════════╗   ╔═══════════╗   ╔═══════════╗
        ║  EVENT    ║   ║  AGENT    ║   ║  REFLECT  ║   ║  NOTIFY   ║
        ║   LOG     ║   ║  STATE    ║   ║  ENGINE   ║   ║   BUS     ║
        ╚═══════════╝   ╚═══════════╝   ╚═══════════╝   ╚═══════════╝
        every action    per-user        the loop that    in-app feed,
        as a fact       memory +        wakes on         later push +
        (saved, skip,   journal +       events &         email
        play, pause)    user model      idle ticks
```

### 3.1 Event log — the substrate
A `events` table where **every meaningful thing that happens is appended**. Source saved, source ingested, episode drafted, episode played, episode skipped, episode completed, user dismissed an idea, user changed a setting. Append-only.

This is the data the companion *thinks about*. It is also incidentally a great audit trail and analytics source.

Postgres for now. EventBridge / Kinesis later if the volume warrants it.

### 3.2 Agent state — per-user persistent memory
A `companion_state` table holding a structured JSON blob per user. Working model of who the user is and what the agent is currently thinking about for them.

Plus `agent_journal` — append-only notes the agent writes to itself, visible to the user.

Plus `episode_listens` — aggregated per-episode listening stats derived from events.

### 3.3 Reflect engine — the thinking loop
A new kind of worker. Two trigger modes:

- **Event-driven**: a `source_ingested` event fires → reflect engine wakes for that user → re-evaluates open threads, optionally drafts, optionally writes a journal entry.
- **Time-driven**: every 4 hours (idle reflection) and every 24 hours (deep reflection / overnight consolidation).

Crucially: the reflect engine is **agentic**, not pipeline. It is a Claude call with tool use. The agent decides what to do each cycle — it might do nothing, it might draft, it might just take a note. We do not script the reflection; we give the agent a system prompt, current state, recent events, and a toolbox.

### 3.4 Notification bus — proactive surface
Today's UX is pull (user opens app). Companion needs push.

Start small: a `notifications` table + `GET /feed` endpoint. The in-app feed is where the agent's nudges, journal entries, and ready-drafts appear. This avoids permission/auth/delivery complexity of true push notifications.

Add later: web push, email digests, mobile push. Each is its own delivery channel feeding off the same notification bus.

---

## 4. Schema additions (on top of BACKEND_PLAN §6)

```sql
-- Append-only event log
CREATE TABLE events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type            TEXT NOT NULL,           -- e.g. source_saved, episode_played, episode_skipped
    subject_type    TEXT,                    -- e.g. source, episode, idea
    subject_id      UUID,
    payload         JSONB DEFAULT '{}'::jsonb,
    correlation_id  UUID,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX events_user_created_idx ON events (user_id, created_at DESC);
CREATE INDEX events_type_idx         ON events (type);

-- Per-user agent state (one row per user)
CREATE TABLE companion_state (
    user_id             UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    state               JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_reflection_at  TIMESTAMPTZ,
    reflection_count    INT NOT NULL DEFAULT 0,
    updated_at          TIMESTAMPTZ DEFAULT now()
);

-- Agent's own notes — visible to user
CREATE TABLE agent_journal (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,            -- thought | observation | nudge | recap
    body            TEXT NOT NULL,
    visible_to_user BOOLEAN DEFAULT TRUE,
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX agent_journal_user_idx ON agent_journal (user_id, created_at DESC);

-- Aggregated listening stats per (user, episode)
CREATE TABLE episode_listens (
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    episode_id          UUID NOT NULL REFERENCES generation_runs(id) ON DELETE CASCADE,
    first_played_at     TIMESTAMPTZ,
    last_played_at      TIMESTAMPTZ,
    completed           BOOLEAN DEFAULT FALSE,
    completion_pct      REAL DEFAULT 0,           -- 0.0 to 1.0
    skip_count          INT DEFAULT 0,
    play_count          INT DEFAULT 0,
    PRIMARY KEY (user_id, episode_id)
);

-- Notifications the user sees in their feed
CREATE TABLE notifications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,            -- draft_ready | nudge | recap | system
    title           TEXT NOT NULL,
    body            TEXT,
    cta             JSONB,                    -- {action: 'open_episode', target_id: '...'}
    read_at         TIMESTAMPTZ,
    dismissed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX notifications_user_idx ON notifications (user_id, created_at DESC)
    WHERE dismissed_at IS NULL;
```

Note: `episodes` is still aliased to `generation_runs` with `status='ready'` (per BACKEND_PLAN). The `episode_id` foreign key in `episode_listens` and `notifications.cta` references that.

---

## 5. File structure additions

On top of BACKEND_PLAN §7:

```
curia-main/
├── companion/                                ← NEW
│   ├── __init__.py
│   ├── reflect.py                            ← the agentic loop
│   ├── prompt.py                             ← the companion system prompt
│   ├── tools/                                ← tool implementations the agent can call
│   │   ├── draft_episode.py
│   │   ├── update_user_model.py
│   │   ├── open_thread.py
│   │   ├── close_thread.py
│   │   ├── write_journal.py
│   │   ├── send_nudge.py
│   │   └── do_nothing.py
│   └── triggers/
│       ├── event_driven.py                   ← invoked from event emit
│       └── scheduled.py                      ← invoked from cron
│
├── core/
│   └── events.py                             ← NEW (emit + query)
│
├── api/
│   └── routes/
│       ├── feed.py                           ← NEW (agent journal + notifications)
│       ├── events.py                         ← NEW (frontend posts listen events)
│       └── companion.py                      ← NEW (read-only access to state, for UI)
│
└── worker/
    └── handlers/
        └── reflect.py                        ← NEW (invoked by reflect_engine triggers)
```

The companion lives in its own top-level package. It depends on `core/`, `intelligence/`, and `studio/` but is not depended on by them — keeps the dependency graph clean and lets v1 ship without it.

---

## 6. API additions

```
# Events (frontend posts user actions; backend emits server-side events too)
POST   /events                    body: {type, subject_id?, payload?}
                                  → 202 (e.g. when user starts/stops playback)

# Feed — what the companion has been doing for me
GET    /feed                      → notifications + recent journal entries, merged + sorted
GET    /feed/journal              → just the agent's journal entries
GET    /feed/notifications        → just notifications (read/unread)
POST   /feed/notifications/:id/read
POST   /feed/notifications/:id/dismiss

# Companion — what does it think it knows about me
GET    /me/companion              → companion_state.state (read-only, for transparency UI)
GET    /me/companion/threads      → currently open threads (themes the agent is watching)

# Episode feedback — fast-feedback loop into the user model
POST   /episodes/:id/feedback     body: {action: 'liked'|'disliked'|'too_long'|'wrong_format'}
                                  → emits event, updates companion model

# Manual nudge to the companion (debug + power user)
POST   /me/companion/reflect      → enqueues a reflect job for current user, returns job_id
```

---

## 7. The reflect engine, in detail

This is the core new thing. Worth specifying carefully.

### 7.1 Trigger model

**Event-driven triggers** (fast cycles, ~minutes after the event):
- `source_ingested` (every Nth — say every 3rd — to avoid storming on bulk ingest)
- `episode_completed` (user finished a full episode — strong signal)
- `episode_skipped` (user dropped early — strong signal)
- `idea_dismissed` (user said no to a draft — feeds the model)

**Time-driven triggers** (steady cycles, even with no events):
- Every 4 hours per active user — light reflection
- Every 24 hours per user — deep reflection / consolidation pass

Active = user has had ≥1 event in the last 14 days. Inactive users skip cycles to save cost.

### 7.2 The cycle (pseudocode)

```python
async def reflect(user_id: UUID, trigger: str, correlation_id: UUID):
    state = await load_companion_state(user_id)
    events = await recent_events(
        user_id, since=state.last_reflection_at, limit=200
    )
    archive_summary = await summarize_archive(user_id)
    listening_summary = await summarize_listening(user_id, window_days=14)

    response = await claude.reflect(
        model=COMPANION_MODEL,                      # haiku for cost; sonnet for deep cycles
        system=COMPANION_PROMPT,
        messages=[{
            "role": "user",
            "content": json.dumps({
                "trigger": trigger,
                "state": state.state,
                "recent_events": events,
                "archive_summary": archive_summary,
                "listening_summary": listening_summary,
            }),
        }],
        tools=COMPANION_TOOLS,                      # see §8
    )

    for tool_call in response.tool_calls:
        await dispatch_tool(tool_call, user_id, correlation_id)

    state.last_reflection_at = now()
    state.reflection_count += 1
    await save_companion_state(state)
```

The agent is given:
- Its current state (what it thinks about this user)
- Recent events (what's happened since last cycle)
- A summary of the archive (size, themes, freshness)
- A summary of recent listening (what's working, what's not)

And it picks tool calls. Sometimes the right answer is `do_nothing` and that's fine.

### 7.3 Cost & cadence sanity check

- Light cycle: Haiku call, ~5k tokens in, ~1k out → ~$0.005/cycle
- Deep cycle (daily): Sonnet, ~20k in, ~3k out → ~$0.10/cycle
- For 100 active users: ~$0.50 per light tick × 6 ticks/day = $3/day light
- Daily deep × 100 users = $10/day deep
- ≈ **$400/month for 100 active users**, before any drafts

Affordable for alpha; needs cost controls (per-user daily budget cap, less frequent cycles for low-engagement users) before scaling.

---

## 8. The tool catalogue

Tools the reflect agent can call. Each is a small, focused action — not a pipeline.

| Tool | Purpose | Input schema | What it does |
|---|---|---|---|
| `update_user_model` | Refine preferences | `{updates: {key: value}}` | Patches `companion_state.state.preferences` |
| `open_thread` | Register an emerging theme | `{theme, source_ids, rationale}` | Adds to `companion_state.state.open_threads` |
| `close_thread` | Mark a theme done/stale | `{thread_id, reason}` | Removes from open_threads, journals why |
| `draft_episode` | Generate a candidate episode | `{thread_id?, source_ids?, format, angle}` | Enqueues the existing `generate_episode` worker job; the resulting episode appears in the feed as a draft |
| `write_journal` | Note something for the user to see | `{kind, body}` | Inserts into `agent_journal` |
| `send_nudge` | Surface a notification | `{kind, title, body, cta?}` | Inserts into `notifications` |
| `do_nothing` | Explicit no-op | `{reason}` | Logs reasoning to journal (private), no other action |

Why `do_nothing` is explicit: the cycle is metered, and we want the agent to be comfortable staying quiet when there isn't anything worth surfacing. Without an explicit no-op tool, models tend to over-act.

`draft_episode` is the bridge — it enqueues the existing episode generation worker. The companion does not duplicate the pipeline; it triggers it. When the worker finishes, the resulting episode lands in the feed as a draft (linked to the journal entry that proposed it).

---

## 9. The companion's system prompt — design principles

(Full prompt is a separate artifact when implementation begins. Principles below.)

- **Identity stable, voice subtle.** A consistent register — observant, low-key, slightly opinionated. Not chirpy. Not corporate. Reads like an attentive editor.
- **Transparency over magic.** When it nudges or drafts, it explains *why* in the journal entry. The user can always see the reasoning.
- **Bias toward silence.** Speaking up has a cost. Default to `do_nothing` unless there's a real signal. Better to under-act than over-act.
- **Long memory, short-term humility.** It uses prior state liberally; it doesn't claim certainty about new patterns until the data supports them.
- **No hallucinated continuity.** It only references past observations that are actually in `agent_journal` or `companion_state`. It does not invent shared history.

---

## 10. Phased build path

Each phase produces something demoable before moving on. None of these block BACKEND_PLAN v1; they all come **after** v1 ships.

### Phase A — Substrate (~1 week)
- `events` table + `core/events.py` emit/query API
- All existing API endpoints emit relevant events
- `companion_state` and `agent_journal` tables created (empty)
- **Exit:** every user action shows up in the event log; UX unchanged.

### Phase B — Listening telemetry (~1 week, requires frontend cooperation)
- `POST /events` for frontend to post `episode_played`, `episode_paused`, `episode_skipped`, `episode_completed`, `episode_scrubbed`
- `episode_listens` aggregation
- Surface listening stats on `GET /episodes/:id`
- **Exit:** play an episode → see `played_at` + `completion_pct` in DB.

### Phase C — Idle reflection, observe-only (~1 week)
- Reflect worker, time-driven only (every 6h per active user)
- Two tools live: `update_user_model`, `write_journal`. No drafts yet.
- `GET /feed/journal` returns entries
- **Exit:** after 1–2 days of usage, journal shows real observations.

### Phase D — Proactive drafts (~2 weeks)
- Add `draft_episode`, `open_thread`, `close_thread`, `send_nudge`, `do_nothing` tools
- Drafts appear in `GET /feed` as cards
- User can accept, regenerate, or dismiss
- Dismissals feed back into model
- **Exit:** save 4 thematically-linked articles → draft appears within 6h.

### Phase E — Event-driven reflection (~1 week)
- Reflect engine wakes on key events (every 3rd ingest, every completed episode, every skip, every dismiss)
- Faster feedback loops; companion feels alive
- **Exit:** save the 4th thematic article → journal entry appears within minutes.

### Phase F — Personality polish (~1 week, ongoing)
- Companion has a name and stable writing voice
- Optional cosmetic UI treatment in the app (this is where BUDDY-shaped design choices live, if we want them)
- Daily/weekly recap notifications
- **Exit:** the companion feels like *someone*, not just a process.

### Phase G — Long-term memory consolidation (~2 weeks)
- Overnight: roll daily events into weekly summaries, weekly into monthly
- Long threads — themes that persist across months
- "What you've explored this season" recaps
- **Exit:** open the app after a week away, get a meaningful "while you were gone…" summary.

### Phase H — Push channels (later)
- Web push, email digests, mobile push (when there's an app)
- All consume from the existing `notifications` table; just new delivery
- **Exit:** user gets the morning digest in email even if they haven't opened the app.

---

## 11. Decisions that shape the build

These are the few choices that cascade into everything else. Worth pinning down before coding starts.

### 11.1 Identity model
Three options:
- **Faceless** — "Curia noticed…" Brand-as-companion. Safest. Forgettable.
- **Light personality** — A name and a stable tone in the journal. *("Caspar, your producer.")* Best ratio of effort to memorability.
- **Full character** — Backstory, idiosyncrasies, occasional opinions of its own. Highest risk/reward. The BUDDY-shaped end of the spectrum.

**Recommendation:** start with light personality. The name and voice can be added in Phase F without touching anything underneath. Full character is a v3 question, not a v1 question.

### 11.2 Push surface for v1 of the companion
- **In-app feed only** — simplest. No permission UX, no email infra. Companion is invisible until you open the app.
- **In-app + email digest** — adds reach. Requires SES + an unsubscribe flow.
- **Full multi-channel** — overkill for alpha.

**Recommendation:** in-app feed only through Phase E. Email digests in Phase H.

### 11.3 Where does the alpha demo stop?
- Stopping at **Phase C** = the system observes silently, builds a model, but doesn't generate proactively. Visible only in the journal feed. Subtle.
- Stopping at **Phase D** = first real "wow." Drafts show up unprompted. This is where alpha users feel the difference.
- Stopping at **Phase F** = polished, named, has a feel.

**Recommendation:** Phase D is the right alpha demo target. Phases A–C are foundational scaffolding without which D doesn't work.

### 11.4 Cost ceiling per user per day
The reflect cycles cost real money. Without a cap, an active user could trigger many cycles per day across event-driven and time-driven triggers.

**Recommendation:** hard cap of ~$0.20/user/day for v1. If we hit the cap, suppress further reflect cycles until tomorrow. Visible in `companion_state` so debugging is easy.

### 11.5 What does the user see vs not see
The agent's full state is large and includes derived stuff that would be confusing to expose raw. We have to decide what's user-visible:
- **Always visible**: `agent_journal` entries with `visible_to_user=true`, all `notifications`, currently open threads.
- **Visible on request** (transparency mode): the user model — preferences, listening stats, current focus.
- **Never visible**: full reflection trace, internal scoring, private agent thoughts (`visible_to_user=false`).

This split lets us be transparent without overwhelming.

---

## 12. Open questions / risks

Things we don't have answers to yet — flag for discussion, not for solving today:

- **Cold-start problem.** A brand new user has no events. The companion has nothing to reflect on. What does it do for the first week? (Probably: nothing meaningful, and a journal entry saying "still learning your taste — save a few things and I'll catch up.")
- **The "creepy" line.** Insight that's too sharp early on feels surveilling. Calibration question: how much does the companion *show it knows* vs hold back?
- **Failure modes.** What if the agent generates a bad draft 5 times in a row? How does the user signal "stop trying that" without giving up on the companion entirely?
- **Multi-device telemetry.** If listening events come from web + mobile, dedup and consistency are real problems.
- **Privacy / data model.** Listening data is intimate. Encryption at rest is mandatory before this ships beyond alpha. Cross-user aggregation (Phase ∞) is a separate consent regime.
- **Cost predictability.** Active users drive the bill. Rate limits and budget caps are pre-launch requirements, not nice-to-haves.
- **Shipping cadence.** Every cycle, the agent might write to the user's feed. Frequency too high feels noisy; too low feels dead. The right cadence is probably *less than the agent wants to* — explicit pacing controls in the prompt.
- **Determinism.** Two reflect cycles on similar state can produce different actions. Is that good (variability = aliveness) or bad (inconsistent UX)? Probably we want temperature low for tool selection and higher only inside `write_journal`.

---

## 13. How this relates to BACKEND_PLAN.md

BACKEND_PLAN ships v1: API + Worker + Postgres. That work is **load-bearing for this** — without it, the companion has nothing to be a companion *to*.

This thesis is the **next direction**, not a parallel one. Order of operations:

```
1. Ship BACKEND_PLAN v1 alpha     ← the vending machine (good, useful, ships)
2. Phase A (events substrate)      ← invisible foundation
3. Phase B (listening telemetry)   ← invisible foundation
4. Phase C (silent reflection)     ← first signs of life in the journal
5. Phase D (proactive drafts)      ← the "wow" — companion goes live
6. Phases E–G                       ← deepens the experience
7. Phase H                          ← pushes outside the app
```

Total elapsed time from end-of-v1 to "Phase D demoable" is ~5 weeks of focused work. Reasonable sprint shape.

If at any point during the companion build it becomes clear this isn't working — the journal feels noisy, drafts are bad, users dismiss everything — the architecture is **layered**, not entangled. Pull the reflect worker, the system reverts to the vending machine. No data loss, no rollback. The event log and listening data stays useful for analytics either way.

---

## 14. Summary

Curia is a curatorial product. Curatorial products are taste-driven. Taste is built through observation over time. So the system needs to observe over time — which means a continuous companion, not a request/response API.

The path:
- Build the request/response API first (BACKEND_PLAN, v1).
- Layer a substrate (events + agent state) on top — invisible to users.
- Add a reflection loop that observes silently — barely visible.
- Give the loop tools, including `draft_episode` — visibly proactive.
- Add personality, push channels, long-term memory — companion-shaped.

None of this is exotic infrastructure — it's all stuff the BACKEND_PLAN scaffolding already supports. What's new is **the agentic reflection loop** as a primitive in the system, sitting alongside the existing job queue.

That's the thesis. The next doc — *Future Thesis 2* — will probably be about how the *frontend* needs to change to host a companion (a feed-first UI rather than a CRUD-first UI). But that's later.
