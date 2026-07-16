# Curia Daily Brief — Pipeline Deep Dive

Audience: CTO / engineering team. Purpose: a ground-truth account of what the
`daily-brief-harness` codebase actually does today, stage by stage, based on
reading the running code (not the design docs sitting next to it — several of
those are stale or describe a superseded design; discrepancies are called out
explicitly). UI/CSS is intentionally out of scope; this is about the pipeline
and the product experience it produces.

---

## 1. What this feature is

Curia's core loop is "user saves/shares a link → app turns it into an audio
show." Daily Brief extends that into a standing, daily product: every user
gets a short personalized audio news show, generated once a day, without them
having to save anything that day.

Per `constitution.md`, the brief is supposed to be built from two ingredients:

1. The user's own interests, inferred primarily from their **recently saved
   links**, surfaced as 3–4 stories ranked by how groundbreaking they are.
2. News relevant to the user's **location** (defaults to India, can be a
   city).

Hard constraints from the spec: the spoken content (excluding the intro) is
capped at ~650 words; the intro is generated separately at playback time
(never baked into the stored transcript, so weather/time-of-day are always
fresh); and the system must never re-tell the same real-world story to the
same user twice — genuine follow-ups are fine, restatements are not.

**Where the implementation has actually landed differs from that spec in one
important way**, detailed in §6 — read that section before treating this repo
as a literal implementation of `constitution.md`.

---

## 2. Two runtime surfaces, one pipeline

The FastAPI app (`app/main.py`) serves two consumers of the exact same
pipeline code (`app/services/pipeline.py`):

- **Test Harness** — a dashboard at `/` for manually driving the pipeline one
  stage at a time, editing prompts live, and inspecting/rating output. This
  is a deliberate engineering tool, not a customer-facing surface (see §7).
- **Production Batch Runner** — `POST /internal/run-brief-batch`, triggered
  by a Railway cron job (intended 5am UTC), authenticated via an
  `X-Internal-Key` header matched against `INTERNAL_SECRET`. It pulls the
  full brief-enabled user list from `curia-backend`
  (`GET {CURIA_BACKEND_URL}/internal/brief-context/all`) and fans out
  `generate_brief_for_user()` (`app/services/brief_runner.py`) across all of
  them, capped at 5 concurrent, 600s timeout each.

Both surfaces call the *same* `PipelineManager` methods in `pipeline.py`, so a
bug fixed/found in the harness is fixed in production and vice versa — there
is no forked "prod version" of the scoring or writing logic.

---

## 3. The pipeline, stage by stage

Each stage is its own module, callable independently, with its own request
model. The harness exposes every stage as its own numbered "step card" you
can re-run in isolation or cascade from any point forward — this modularity
is intentional and load-bearing for how the team iterates on prompts.

```
User Profile (Beats, subtopics, custom topics, location, country, saves)
        │
        ▼
Stage 1  news_service.py          — RSS fetch across Google News, no LLM
        │
        ▼
Stage 2  scoring_service.py       — no-LLM rank/pre-filter (toggleable)
        │  (enrichment_service.py runs inside this stage, optional)
        ▼
Stage 3  prompts.py + llm_service — Score & Curate (single combined LLM call)
        │
        ▼
Stage 3b enrichment_service.py    — full-text scrape for the 5 winners only
        │
        ▼
Stage 4  prompts.py + llm_service — Narrative Outline (LLM, structure only)
        │
        ▼
Stage 5  prompts.py + llm_service — Transcript (LLM, the actual script)
        │
        ▼ (only at playback time, independently)
Stage 6  prompts.py + llm_service — Intro generation (weather/time/calendar)
        │
        ▼
Final assembly = Intro + Stage 5 transcript, in order
```

### Stage 0 — User profile / inputs

Not a network call — the shape everything downstream is keyed on
(`UserProfile` in `pipeline.py`):

- `interests`: an **ordered list of "Beats"** (see Stage 1) — position in the
  list is priority rank, not just membership.
- `subtopics`: optional, per-Beat, user-typed free-text keywords (e.g. under
  the "Tech" Beat, a user might type "Anthropic", "OpenAI") — these narrow
  that Beat's own search, additively, never replacing the Beat's base feed.
- `custom_topics`: freeform strings, structurally independent of Beats —
  each gets its own dedicated fetch and is never dropped by any downstream
  filter (see Stage 2).
- `location` (freeform, e.g. "Mumbai") drives the **Local Pulse** slot only.
- `country` (ISO-3166-1 alpha-2, e.g. "IN") drives which national *edition*
  of Google News a Beat's own fetch uses — entirely separate mechanism from
  `location`.
- `saves`: the user's saved links. **Important**: as of the current code,
  this field does not influence article sourcing or ranking at all — see §6.
- `structure` / `voice`: pick Stage 5's narrative shape and tone.

### Stage 1 — Article sourcing (`news_service.py`, no LLM)

Pulls raw Google News RSS via direct `urllib` + XML parsing (the `gnews`
package in `requirements.txt` is a leftover dependency — it's not actually
used; the service hand-rolls its own RSS client).

**Beats.** The topic taxonomy the user actually picks from is 7 broad
"Beats" — `Tech`, `Business`, `World`, `Science & Health`, `Culture`,
`Lifestyle`, `Sports` — each mapped to one or more of Google's own curated
topic-vertical feeds (the same feeds behind news.google.com's own nav tabs,
verified to be recency-heavy: one Beat's feed measured 92% of items within
30 hours). `Lifestyle` has no matching Google vertical, so it's the one Beat
that's a permanent hand-written OR-keyword search instead. This taxonomy is
itself the second iteration of the topic model — see §6 for the lineage from
11 raw gnews topics → a proposed 21-category IAB taxonomy → these 7 Beats.

**Only the top 4 active Beats are ever queried** (`MAX_ACTIVE_BEATS = 4`) —
a deliberate precision-over-coverage tradeoff; anything ranked 5th or lower
in the user's interest list gets zero requests.

**Geography is two independent axes:**
- *Per-Beat national edition* (`BEAT_GEO_MODE`): each Beat is tagged
  `global` (US edition only — World, Tech, Science & Health, Sports),
  `mixed` (US + the user's own national edition, merged — Business,
  Culture, since these carry real region-specific coverage the US edition
  misses), or `national` (home edition only — Lifestyle). This is driven by
  the `country` field, defaulting to India if unset.
- *Local Pulse* (driven by `location`, independent of the above): one query
  per active Beat, that Beat's own curated anchor terms **AND'd** with the
  location, tagged `Local: {location}`. Every term is wrapped in Google's
  `intitle:` operator rather than plain relevance search — verified live
  that plain search on a Tech query surfaced "Man Drowns After Falling Into
  Manhole... In Mumbai" (zero tech relevance, just trending-in-Mumbai noise).
  `intitle:` forces literal presence of the term in the headline. There's
  also a measured hard cliff: once a location-scoped query string crosses
  ~190–200 characters, Google silently drops the location filter entirely
  and the query reverts to generic relevance matching — so each Beat fires
  its *own* short local query rather than one merged cross-Beat query.

**Custom topics** are structurally outside the Beat/interest system — each
one is its own single, independent, always-fetched query, tagged
`Custom: {topic}`, and (as covered in Stage 2) always survives filtering
regardless of score, since there's no generic topic anchor to fairly score a
user-typed phrase against.

**Discovery/serendipity**: there is currently no discovery/filter-bubble-
breaking slot in the live pipeline (older design docs describe one; it's not
in the current `news_service.py`/`prompts.py`).

**Dedup and fallback**: results are deduped by exact URL and normalized
title across every query fired. If the total pool drops below 4 articles
(network failure, etc.), 4 hardcoded high-quality mock articles are appended
so the pipeline never dead-ends. The final pool is shuffled before returning.

### Stage 2 — No-LLM pre-filter rank (`scoring_service.py`, optional)

This stage exists purely to cut the LLM's input size before Stage 3, and is
explicitly *not* trying to produce the final ranking — it only needs to be
recall-safe enough that dropping the bottom half doesn't lose anything that
actually mattered. It's a toggle in the harness (`step2bEnabled`); production
(`brief_runner.py`) always runs it.

Runs entirely locally, no API calls:

- **Topic similarity (TS)**: a frozen `all-MiniLM-L6-v2` sentence-transformer
  embeds each article's title+description and compares it against a set of
  concrete, headline-style "scenario" anchor sentences per Beat (deliberately
  *not* abstract category descriptions — measured roughly 2x better cosine
  similarity from concrete phrasing, e.g. "a chipmaker launching a new
  processor" over "semiconductor industry news"), and deliberately **no
  named companies in any anchor** — an earlier version that hardcoded
  entities like OpenAI/NVIDIA was found to not generalize (a real "Claude
  Sonnet 5" headline scored *lower* against an anchor naming Anthropic than
  against a generic "an AI lab" anchor) and to bake in a permanent bias
  toward whichever companies happened to be on the list.
- **Same-story clustering**: greedy clustering on cosine similarity above a
  threshold collapses every outlet covering one event into a single
  representative (the highest-TS member); cluster size feeds directly into
  the significance score below. Local (`Local:`) articles get their own,
  separate clustering pass for dedup only — they are never topic-scored or
  cut (see below).
- **Real-world significance (RS)**: half from cluster size (more outlets
  covering the same event → more likely to matter), half from title-level
  heuristics — a regex-based "story type" classifier (launches/funding
  rounds score high; stock-surge/opinion pieces score low) plus a dollar-
  amount normalizer. This half exists specifically to catch single-sourced
  but genuinely important stories that cluster size alone would zero out.
- **Multi-topic boost**: an article that clears the similarity threshold on
  more than one active Beat gets an additive, capped bonus (reflecting that
  a genuine intersection story — e.g. AI + Semiconductors + Startups — is
  more valuable than a single-topic one; capped so several *weak* multi-hits
  can't out-rank one strong single-topic match).
- **The cut**: composite = `TS × 0.55 + RS × 0.45`. Only the top
  `keep_fraction` (default 50%) of the **non-local** pool survives into
  Stage 3. `Local:` articles are always kept regardless of score. `Custom:`
  articles are always kept too — they have no topic anchor to be scored
  against fairly, so cutting them would defeat the point of a user typing
  one in explicitly.
- Deliberately **no hard source-allowlist and no hard-negative-phrase gate**
  (both existed in an earlier iteration — see §6 lineage) — both were
  exclusionary by construction and risked silently killing a real article on
  a title collision. Everything here only produces gradations, never a hard
  zero, except the keep_fraction cut itself.

**Enrichment (`enrichment_service.py`), inside this same stage, optional
(`ENABLE_ARTICLE_ENRICHMENT`, default on):** before the cut is computed, the
service tries to decode each Google News redirect to the real publisher URL
(`googlenewsdecoder`), fetch the page, and extract real body text
(`trafilatura`) — then re-embeds using that real lead paragraph instead of
the thin RSS description, and re-scores. This is "cluster-borrowing": a
3-outlet cluster only needs *one* member's page to be scrapeable, so all
members are raced concurrently and whichever succeeds first wins. Any
failure (blocked, timed out, disabled) leaves that entry exactly as the
title-only scoring computed — this is a pure best-effort accuracy refinement,
never a required step. After enrichment, a second lightweight re-clustering
pass runs, because richer text reliably pushes genuine duplicates over the
clustering threshold that title-only text missed (a measured example: two
outlets' funding stories went from 0.55–0.72 cosine on titles alone to
0.75–0.90 using scraped lead paragraphs).

### Stage 3 — Score & Curate (single combined LLM call)

This used to be two separate LLM calls (score, then curate into slots); it's
now one call (`SYSTEM_SCORE_CURATE_PROMPT` in `prompts.py`). Every article
entering this stage is tagged with a stable numeric `id` so the model can
reference "which article" by copying back an integer rather than
reproducing a long title verbatim — the only reliable way to look an article
back up afterward.

The prompt asks the model to act like a wire editor, in a fixed order:

1. **Set local articles aside** (anything tagged `Local:`).
2. **Deduplicate events, not just headlines** — the model is explicitly told
   to treat two articles as the same event if they report the same
   real-world occurrence, "even if they come from different publications,
   use different headlines, focus on different details, or were published at
   different times," and to keep exactly one representative per event. A
   follow-up only counts as a new event if it's a substantial new
   development, not just fresh commentary on the same thing.
3. **Rank comparatively, not numerically** — this is a deliberate design
   choice: rather than asking the model to output independent numeric scores
   per dimension (the older approach, see §6), it's asked to think "if I
   could only tell my audience four things that happened today, what would
   they be and in what order?" and produce a full ranked ordering of every
   deduplicated non-local story, judged on topical relevance and real-world
   significance, with an explicit instruction not to confuse popularity with
   significance and not to penalize a real story for coming from a smaller
   outlet.
4. **Rank local articles the same way**, separately, picking one winner.
5. **Final validation pass** built into the prompt itself: re-check that no
   two selected stories describe the same underlying event before returning.

Output is the full ranked order (not just top 4) for every non-local story,
plus the full local ranking — `pipeline.py` then takes only the winning
slice: ranks 1–4 become `Main Story` / `Supporting Story 1-3`, local rank 1
becomes `Local Pulse`, and the model's own echoed `id` is used to look the
real URL/source/description back up from the original pool (never trusting
title-matching). This bridging step exists so nothing downstream — Stage 4,
history storage, the dashboard — has to know about the model's actual output
schema.

### Stage 3b — Full-text fetch for the winners (`enrichment_service.py`, reused)

A small, separate step: now that exactly 5 articles have been chosen, fetch
each one's *real* body text (not just the thin RSS snippet) so Stage 4/5 can
write from actual reporting. Reuses the identical decode→fetch→extract
pipeline from Stage 2's enrichment. Each selection's own URL is tried first;
if that fails, the same-story alternates recorded during Stage 2's
clustering (other outlets that covered the exact same event) are raced
concurrently as fallback candidates. Only if the primary URL *and* every
alternate fail does that one selection quietly fall back to its existing
short description — this never blocks or fails the other 4 selections.

### Stage 4 — Narrative Outline (LLM, structure only, no prose)

`SYSTEM_OUTLINE_PROMPT` deliberately does not let the model write any
narration. It only decides: which fixed segment types to use
(`NEWS_LEAD`, `NEWS_STANDARD_1/2/3` — all three mandatory — plus `LOCAL` and
`OUTRO`, and `NEWS_GLIMPSE` only for the Preview structure), what facts each
segment *must* cover versus what's optional, what to deliberately omit, and
a hard word budget per segment. The instruction is explicit that this
outline should be detailed enough that a *different* model could write the
full script from it without re-reading the source articles. Total budget:
750–850 words targeted, 700–900 as hard outer bounds (never below/above
regardless of anything else); `LOCAL` is capped at 60–70 words; the lead
story gets the largest allocation.

### Stage 5 — Transcript writing (LLM, the actual script)

`SYSTEM_TRANSCRIPT_PROMPT` treats Stage 4's outline as authoritative — every
`required` `must_cover` point has to appear, `omit` items must not, segment
order and word budgets are fixed — but instructs the model to restate each
point in its own words rather than lightly rewording the outline's bullet
phrasing.

- **Structure** (`Structure 1` "The Preview" adds a `NEWS_GLIMPSE` teaser up
  front; `Structure 2` "The Lead" jumps straight into the lead story).
- **Voice**: two tonal presets — a casual, contraction-heavy "Smart Friend"
  and a more reflective, question-driven "Curious Companion."
- A large fraction of the prompt is a style guide aimed at *not* sounding
  like a news broadcast — an explicit banned-phrase list ("Turning now to…",
  "Experts believe…", "This comes as…"), a rule that consecutive news
  segments must be joined by a short, never-repeated conversational bridge
  rather than a jump-cut, and a "self-check" instruction to the model to
  read its own output back and flag anything that sounds like an anchor.
- **Personalization** (from `saves`) is allowed here, and only here, and
  only if genuine — "never force personalization... if there's no
  meaningful connection, ignore those fields." This is the *only* place in
  the current pipeline where saved links do anything at all (see §6).
- Output format is plain text with mandatory bracket tags
  (`[NEWS_LEAD]`, `[LOCAL]`, etc.) that `pipeline.parse_transcript_segments()`
  regex-splits into structured segments; there's a lenient fallback parser
  for near-miss formatting and a last-resort single-blob fallback so a
  malformed response never crashes rendering.
- **Automatic word-count correction**: if a real (non-simulated) response
  lands outside the hard 700–900 word bound, `pipeline.py` does one
  automatic corrective retry — sending the same request back with an
  explicit "you were N words, that's too short/long, rewrite the full
  script with the same structure and facts but expand/trim" instruction —
  rather than silently shipping an out-of-spec script.

### Stage 6 — Intro generation (separate call, playback time only)

Per the constitution's explicit design, the intro is never part of the
stored transcript or its word budget — it's generated fresh at the moment a
user presses play, so it can't go stale. `main.py`'s `/api/pipeline/intro`
endpoint enriches the request before calling the LLM:

- **Weather + local time**: WeatherAPI.com `current.json`, keyed by
  `WEATHERAPI_KEY`. Falls back to the literal string `"pleasant"` if no key
  is configured or the call fails — failures are logged, never raised, so a
  weather outage never blocks the brief.
- **Calendar**: a Google Calendar service-account integration
  (`calendar_service.py`) reads today's events off a calendar explicitly
  shared with the service account, producing a one-line summary like
  "3 meetings today, starting with Standup at 9:00 AM." Only attempted if
  `GOOGLE_SERVICE_ACCOUNT_FILE` is configured; silently skipped otherwise.

The prompt (`SYSTEM_INTRO_PROMPT`) asks for a 55–75 word, casual, name-first
greeting, grounded in the actual local time of day (never guessed), that
acknowledges the gap since the last brief only if it's been more than a day,
mentions weather with a practical tip, and optionally names the day's
meetings — closely matching the exact example phrasing in `constitution.md`.

### Final assembly

`Full brief the user hears = Intro (Stage 6, generated at playback) + Stage
5 transcript segments, in order.` Nothing in Stages 1–5 has any knowledge of
the intro; Stage 6 has no knowledge of the transcript's content beyond
name/location/time. This separation is intentional and is what makes the
"never stale weather" guarantee possible.

---

## 4. Supporting infrastructure

**LLM execution (`llm_service.py`)** — every Stage 3/4/5/6 call goes through
one `call_llm()` function, model `claude-haiku-4-5-20251001`. Temperature
`0.2` for Stages 3–4 (structured output, wants consistency), `0.7` for Stage
5/6 (prose, wants some texture). Stage 3 gets a much larger `max_tokens`
(12000 vs 4000) since it now returns a full ranked order of the entire
deduplicated pool, not just a top-5. **If no Anthropic key is configured
anywhere** (server env var or a per-request `X-Anthropic-API-Key` header),
the service transparently falls back to canned mock responses for every
stage — this is what lets the harness be fully click-through-able without
burning API credits or requiring a key at all, and it's what "Simulated
Mode" in the dashboard status badge refers to.

**Run history (`history_service.py`)** — every completed harness run is
persisted (Firestore in production/Cloud Run, local JSON files under
`app/data/runs/` in dev — auto-detected by environment, with a
`GOOGLE_CREDENTIALS_JSON` escape hatch for non-GCP hosts like Render) along
with per-step thumbs up/down + comment feedback and per-article relevance
ratings. This is the harness's feedback loop for tuning prompts against
real output over time.

**Production batch runner (`brief_runner.py`)** — the non-harness path,
mirroring Stages 1–5 exactly, then continuing:

1. `db_service.brief_set_generating()` — idempotent insert into a
   `daily_briefs` Postgres table (skips a user who already has a row for
   that date — safe to re-trigger the cron).
2. Stages 1 → 2 → 3 → 4 → 5 exactly as above.
3. `tts_service.synthesize_text()` — Smallest.ai Lightning TTS, text chunked
   at sentence boundaries under a 200-char hard API limit, each chunk
   synthesized sequentially, stitched into one WAV. **Currently broken — see
   §6, this is a real bug, not a design gap.**
4. `storage_service.upload_audio()` — S3/R2-compatible upload.
5. `db_service.brief_set_ready()` — writes transcript, audio URL, duration,
   articles, and outline to the row.
6. `push_service.send_brief_ready()` — Firebase Cloud Messaging push,
   "Your morning brief is ready."
7. Any exception anywhere in this chain is caught per-user (batch runner
   uses `asyncio.gather` + a semaphore of 5) and written to the row as
   `status='failed'` with the error text — one user's failure never blocks
   or fails the rest of the batch.

---

## 5. The dashboard's actual job

Per `constitution.md`, the dashboard's stated purpose is **not** a demo UI —
it's the mechanism for testing the pipeline with configurable, reproducible
inputs. Concretely, that shows up as:

- Every stage is independently re-runnable, and every LLM stage's system
  prompt is a live-editable textarea with "Re-run" (this stage only) or
  "Run From Here" (cascade this stage through to the transcript) — the
  point being that a prompt change can be validated against the *same*
  fetched article pool without re-hitting live RSS.
- A per-article Yes/No relevance widget and per-stage thumbs+comment
  feedback, both persisted with the run, so prompt iteration has an actual
  feedback trail instead of relying on the tester's memory.
- A Runs History Explorer tab for revisiting and reloading any past run's
  full state (inputs, every stage's output, all feedback) back into the
  live workspace.

**One caveat worth flagging against the constitution's explicit
determinism requirement** ("changing only the username and re-fetching
should surface the same article again, same day"): Stage 1 hits live RSS
feeds that change minute to minute, shuffles its output before returning,
and Stage 3 runs at temperature `0.2` rather than `0`. None of these make
the system *wildly* non-reproducible, but none of them are pinned either —
"same inputs, same day" is not currently a guaranteed-identical rerun, just
a likely-similar one. If bit-for-bit reproducibility is actually needed for
testing (e.g. to isolate whether a prompt edit changed the output), the
harness would need an article-pool snapshot/replay mode, which doesn't
exist today.

---

## 6. Where the code has drifted from the spec — for the CTO

These are concrete, code-verified findings, not concerns inferred from the
design docs sitting in the repo root (several of which — see the note at the
end — describe earlier or proposed designs that have since been superseded
or partially superseded by what's actually running).

**1. Saved links no longer drive story selection — this is the biggest
product-shape change from `constitution.md`.** The constitution's whole
framing is "find patterns in the user's saved links" as the *primary* signal
for what news to surface. In the current code, `UserProfile.saves` is threaded
through to exactly one place: Stage 5's transcript prompt, as optional color
("if the listener's interests or saved links genuinely relate to a story,
naturally acknowledge the connection... never force it"). Neither the article
fetch (`news_service.fetch_articles_for_profile`, which takes `interests` and
`custom_topics`, not `saves`) nor the Score & Curate prompt (which receives
`interests` and `location` only) ever look at saves. In effect, the product
has moved from "saves-inferred topics" to "explicit interest chips (Beats) +
location," with saves reduced to a personalization garnish in the final
script. This may well be an intentional pivot the team already made
knowingly — but it's a large enough divergence from the written spec that it
should be a conscious, named decision rather than something that happened
implicitly across several editing passes.

**2. The anti-repeat rule — the constitution's other core requirement — is
not wired up.** `history_service.get_seen_stories(username)` exists and is
exposed as `GET /api/users/{username}/seen-stories`, reading every past
run's curated slots for a user. But nothing calls it before or during Stage
3 — `SYSTEM_SCORE_CURATE_PROMPT`/`USER_SCORE_CURATE_PROMPT` never receive a
"stories this user has already heard" list, so there is currently no
mechanism preventing the same real-world event from being selected again on
a later day. (The one UI hook that looks related —
a "reload seen-stories count on username blur" comment in `app.js` — is an
empty stub: `addEventListener("blur", async () => {})`.) Given this is one of
the two explicit hard requirements in `constitution.md`, it's worth
confirming whether this is a known, tracked gap or something that quietly
fell out during refactors.

**3. TTS is currently broken in production and will silently produce
transcript-only briefs.** `app/services/tts_service.py` calls `logger.info`
/ `logger.warning` in several places but never imports `logging` or defines
`logger` anywhere in the file. The very first `logger.info` call happens
unconditionally near the top of `synthesize_text()`, before any network
call is even attempted — so every invocation raises `NameError` immediately.
`brief_runner.py` does catch this (`except Exception as tts_exc:
logger.warning("[brief] TTS failed, saving transcript-only...")`), so the
batch job doesn't crash — but the practical effect is that **no user has
ever received or will ever receive actual audio** from this runner until
this one-line fix (add `import logging` + `logger =
logging.getLogger(__name__)`) ships. This is worth a same-day fix; it's a
two-line change, not a design question.

**4. Brief length has grown past the constitution's stated cap.** The spec
says the brief content (excluding intro) should be "limited to around 650
words." The current Stage 4/5 prompts target ~800 words with hard bounds of
700–900. Likely an intentional, considered change (650 words is quite tight
for 4 stories + local), but it's a specific, quotable number in the spec
that's now meaningfully different from what ships — worth a conscious
sign-off rather than silent drift.

**5. `gnews>=0.3.6` in `requirements.txt` is dead weight.** `news_service.py`
hand-rolls its own RSS fetching via `urllib` + `xml.etree`; the `gnews`
package is never imported anywhere in `app/`. Harmless, but worth pruning
next time the dependency list is touched.

**6. Two external dependencies this repo assumes but cannot itself verify:**
the `daily_briefs` Postgres table (`db_service.py` executes queries against
it; no migration lives in this repo) and the `curia-backend` endpoint
`GET /internal/brief-context/all` that the batch trigger calls to get the
brief-enabled user list. Both need to actually exist on the backend side —
worth a direct check with whoever owns `curia-backend` before flipping the
Railway cron on for real, rather than assuming they're already there.

**A note on the root-level design docs**: `IAB-categorization-plan.md`
describes a 21-category taxonomy that was tried and then abandoned in favor
of the current 7 "Beats" (the code comment in `news_service.py` calls this
out directly — the IAB categories were "too granular for users to
meaningfully pick from, and too narrow individually to justify a dedicated
query each"). `no-llm-article-ranking.md`, `ranking_system_changes.md`, and
`ranking_system_changes_2.md` are an iteration log for what is now
`scoring_service.py` — most of their final-state proposals (dedup threshold,
roundup-article detection, normalized topic similarity, capped multi-topic
boost) did make it into the live code, though the hard source-allowlist and
hard-negative-phrase gate they describe were deliberately dropped in the
version that actually shipped (see Stage 2 above). `pipeline-reference.md`
and `daily-brief-harness-dash.md` describe an earlier version of the
pipeline (separate Score and Curate LLM calls, numeric 4-dimension scoring,
no Stage 2/3b) that has since been replaced by what's described in this
document — treat those two files as historical, not current.

---

## 7. Quick file map

| Concern | File |
|---|---|
| FastAPI routes, batch trigger | `app/main.py` |
| Pipeline orchestration, request/response schemas | `app/services/pipeline.py` |
| Article sourcing (RSS, Beats, geo, local pulse) | `app/services/news_service.py` |
| No-LLM pre-filter rank | `app/services/scoring_service.py` |
| Article scraping / enrichment (Stage 2 + Stage 3b) | `app/services/enrichment_service.py` |
| All LLM prompts | `app/templates/prompts.py` |
| LLM call wrapper + simulated-mode mocks | `app/services/llm_service.py` |
| Run history + feedback storage | `app/services/history_service.py` |
| Weather/time/calendar for the intro | `app/main.py` (weather) + `app/services/calendar_service.py` |
| Production per-user orchestration | `app/services/brief_runner.py` |
| Text-to-speech | `app/services/tts_service.py` (currently broken — §6.3) |
| Audio upload | `app/services/storage_service.py` |
| Postgres read/write | `app/services/db_service.py` |
| Push notifications | `app/services/push_service.py` |
| Env/config | `app/config.py`, `.env.example` |
| Dashboard frontend | `static/index.html`, `static/app.js`, `static/styles.css` (not covered here by request) |
