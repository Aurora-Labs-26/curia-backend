# Daily Brief — High-Level Summary

Daily Brief generates a short, personalized spoken news briefing for each user. Under the hood it's two pipelines:

- **Generation** — finds articles, decides which ones matter to a given listener, and writes the spoken script.
- **Judging** — checks the generation pipeline's own work, mostly after the fact, without slowing anything down.

The two are independent except for one deliberate link: the **faithfulness judge** for article segments can send a flagged script back to be rewritten before the brief is ever marked "ready." That's the only place a judge's verdict changes what a listener actually receives — everywhere else, judging is an observer, not a gatekeeper.

This document walks through every block in plain language: what it's for, where its input comes from, what it does with that input, and where its output goes next. For exact numbers (word caps, token budgets, model names, thresholds) see `daily_brief_technicalities.md`.

---

## Part 1 — The Generation Pipeline

### 1.1 Pre-Opt (batch prep, ahead of time)

**Purpose**: Do the expensive work for the 7 fixed "Beat" topics (Tech, Business, World, Science & Health, Culture, Lifestyle, Sports) once, in advance, instead of once per user.

**Input**: Nothing user-specific — just the 7 seeded system topics.

**Processing**: For each Beat, independently: fetch fresh articles, rank/dedup them locally, send the survivors to the same editorial LLM call every user's brief uses, fetch real article text for the top picks, and write the spoken segment scripts for the winning 4 stories per Beat.

**Output**: Cached article + segment data, shared by every user who has that Beat as an interest. A re-run replaces the old top-4 for that Beat rather than piling up alongside them.

**Consumer**: The per-user brief trigger (1.2) below — any user who picked a Beat that's already been Pre-Opt'd skips straight to a cache hit for that topic, with zero fetch/rank/LLM cost.

### 1.2 Per-User Brief Trigger — Building the Pool

**Purpose**: Assemble one specific user's candidate article pool for today.

**Input**: The user's chosen system topics (Beats), any custom (free-typed) topics, and their home location.

**Processing**: Three sources get merged into one pool — Pre-Opt's already-cached picks for any chosen Beats (no re-fetch), a fresh fetch for each custom topic, and a fresh fetch for local news at that location. If a Generate Brief already exists for that user today, this whole step is skipped and the existing article selection is reused as-is — only the intro/outro get regenerated.

**Output**: A combined raw article pool.

**Consumer**: Local ranking (1.3).

### 1.3 Local Ranking & Dedup — No LLM Involved

**Purpose**: Cheaply cut the raw pool down to the strongest candidates before spending any LLM budget on it, and collapse duplicate coverage of the same real-world event into one entry.

**Input**: The raw pool from 1.2.

**Processing**: Every article gets embedded and compared against a set of example headline-scenarios for each topic (how well does this actually match "Tech," "World," etc.), plus a cheap heuristic score based on the headline itself (does it sound like a launch, an acquisition, a large dollar figure, a listicle). Articles covering the same real event get clustered together and collapsed to one representative. Roughly half the pool is kept — except local news and custom-topic articles, which always survive this cut regardless of score, since they either have no fair topic comparison to make or are too rare to filter aggressively.

**Output**: A trimmed, deduped, roughly-ranked pool.

**Consumer**: Score & Curate (1.4).

### 1.4 Score & Curate — The Editorial Call

**Purpose**: This is the single most important LLM call in the pipeline — it decides what actually makes it into the brief, and in what order.

**Input**: The trimmed pool from 1.3, plus the user's interests and location.

**Processing**: One LLM call does four things in sequence:
1. Groups near-duplicate articles into one representative per real-world event, and gates each representative — a headline that's really a vague roundup or doesn't genuinely connect to something the user asked for gets excluded here, no matter how "significant" it sounds.
2. Ranks every surviving article by real-world significance (how many people/orgs affected, how lasting, would a major wire service lead with it) — not just topical relevance.
3. Separately evaluates local-news candidates on two conditions that both must hold: genuinely about the user's actual home location, and connected to something they asked about. Zero local stories passing is treated as the correct outcome, not a failure to work around.
4. Runs a final sanity check on its own output before returning it.

**Output**: A full ranked list of every valid article (not just the winners), plus the local pick if any. The pipeline takes the top story as the "lead," the next three as "standard" supporting stories, and the local pick (if any) as a 5th slot.

**Consumer**: Full-text enrichment (1.5) for the ones that don't already have cached scripts; article segment writing (1.6) either way.

### 1.5 Full-Text Enrichment

**Purpose**: Replace a thin headline+one-line-description with the article's real body text, so the script that gets written has something substantial to work from.

**Input**: The 4-5 selected articles.

**Processing**: Follows the Google News redirect to the real publisher URL and scrapes the actual page. If that specific outlet's page can't be reached, it tries other outlets that covered the same event (found during the dedup pass in 1.3) instead of giving up. If everything fails, the segment gets written from the headline/description alone, and is explicitly told not to invent specifics it doesn't have.

**Output**: Real article body text (or an honest "nothing available" signal) per selected article.

**Consumer**: Article segment writing (1.6).

### 1.6 Article Segment Writing — Where Judging Plugs In

**Purpose**: Write the actual spoken script for one article, in a conversational "catching up with a friend" voice — never a news-anchor voice.

**Input**: One article's title, source, and body text (or headline-only, if 1.5 failed).

**Processing**: An LLM writes the segment. Each of the 3 segment types (lead, standard, local) gets its own instructions and its own length budget — the lead gets the most depth, standard and local are progressively shorter. The result is cached by article, so the exact same script gets reused for every future user who picks that same article, rather than rewritten per person.

**This is where the two pipelines connect.** Immediately after writing (or reusing) this script, the **faithfulness judge** checks whether every factual claim in it is true and verifiable in the real world — not just whether it matches the one source article, since the writer is expected to draw on broader knowledge too. If it finds a real fabrication or an unsupported claim, the script gets **rewritten with the specific problem fed back in**, then re-checked — up to twice — before the brief is allowed to say it's ready. If a segment is a cache hit (this exact article was already written for someone else), it's just re-verified against its already-computed verdict rather than rewritten, since a shared/cached script being rewritten now wouldn't be visible to whoever already received it.

**Output**: A finished, fact-checked script, cached for reuse.

**Consumer**: Assembly (1.8) and the article segment cache itself.

### 1.7 Intro/Outro Bookends

**Purpose**: Write the one part of the brief that's genuinely unique to this user, on this specific day — the personal greeting and story preview at the start, and the sign-off at the end. Unlike segments, these are never cached.

**Input**: The user's name, location, current weather and local time, and the day's final story lineup with each story's one-line editorial reason.

**Processing**: One LLM call writes both the opening and the closing. The opening greets the listener by name, references the actual time of day and weather, and previews the stories coming up without giving away details. The closing is a short, warm wrap-up. Both are checked against the same anti-hallucination rule as the segments: don't invent details about the news that weren't actually given.

**Output**: An intro string and an outro string.

**Consumer**: Assembly (1.8). Also judged separately (bookend faithfulness, Part 2) — but never regenerated automatically, since a recurring problem here means the prompt itself needs a human fix, not an automated patch.

### 1.8 Assembly & Delivery

**Purpose**: Put everything together into the final manifest a user actually receives.

**Input**: The intro, the (now fact-checked) segment scripts in broadcast order (lead → standard stories → local), and the outro.

**Processing**: Stitches them into one ordered list with estimated per-segment duration, marks the brief "ready," and archives a full copy for later review.

**Output**: The finished brief — what gets returned to whoever asked for it.

**Consumer**: The end user / harness dashboard. Also fires the automated judging pass (Part 2) in the background right after this — for everything except the article-faithfulness check, which already ran synchronously in 1.6.

---

## Part 2 — The Judging Pipeline

Judging exists on two separate tracks: a small, human-driven calibration workflow, and a fully automated pass that runs on every real brief. Only one judge — article faithfulness — is allowed to change what a listener receives; every other judge is purely observational.

### 2.1 Deterministic Checks (no LLM at all)

**Purpose**: Catch mechanical problems cheaply, without spending any LLM budget.

**Input**: The already-generated brief's segments and metadata.

**Processing**: Plain code checks — is there markdown or a raw URL that shouldn't be spoken aloud, does the text contain a leaked "as an AI, I can't..." refusal, is the segment structure right (exactly one lead, at most 3 standard, at most 1 local), does the intro actually mention the listener's name/location/weather/time of day, is the total estimated runtime in a sane range.

**Output**: Pass/fail/flag per check.

**Consumer**: Logged for review; never blocks anything.

### 2.2 Automated Judges (background, on every real brief)

**Purpose**: Independently sanity-check the pipeline's own editorial and factual decisions, without ever slowing down or blocking the brief the listener is waiting for.

**Input**: The just-completed brief's full data — article pool, rankings, segments, bookends.

**Processing**: Fires automatically right after a brief is already built and returned. Depending on what kind of run it was, it may check: whether the articles the pipeline judged "relevant" actually are (a second opinion, same bar the generation pipeline itself uses), whether the #1-ranked story really is the most significant in the pool and whether the rest are in a sensible order, and whether the intro/outro accurately reflect the user's real name/location/weather/story picks. None of these ever rewrite anything — they're purely a report card, viewable on the dashboard.

**Output**: A verdict per check, logged for the dashboard.

**Consumer**: The "Judge Output" dashboard tab; long-run trend tracking.

### 2.3 Article Faithfulness — Judging That Actually Changes the Output

**Purpose**: Verify every factual claim in an article segment against the real world, and fix it if it's wrong — this is the one exception to "judging never changes what ships." Covered fully in 1.6 above; repeated here because it's the connection point between the two pipelines.

**Processing summary**: Classifies every claim into fabricated/unsupported/unverifiable-but-plausibly-just-fresh/stylistic. A fabricated or unsupported claim triggers a rewrite (fed the specific problem back in) and a re-check, up to twice, before falling back to whichever attempt turned out least bad. A claim that's simply too fresh to be corroborated yet is deliberately never treated as a reason to rewrite — punishing timeliness would be the wrong trade.

**Note**: this judge only ever regenerates a segment the first time it's written (a cache miss). If the exact same article was already generated for an earlier user, later users just inherit whatever verdict that first pass settled on.

### 2.4 Bookend Faithfulness, Tone/Flow, and Brief Coherence

**Purpose**: Round out the automated/manual judging picture — checking the intro/outro against the real inputs they were given, comparing a generated script's tone against a hand-written reference, and checking whether a fully stitched brief reads as one coherent piece rather than a set of disconnected segments stapled together.

**Processing**: Bookend faithfulness runs automatically on every real brief (same "report card, never rewrites" rule as 2.2). Tone/flow and whole-brief coherence are manual-only, part of the calibration workflow below — they require a hand-authored reference script to compare against, which fresh daily news doesn't have.

### 2.5 Manual Gold-Set Calibration

**Purpose**: Establish whether the judges themselves can be trusted, by checking their verdicts against real human judgment.

**Input**: A small number of past runs, deliberately "frozen" (pinned) by a human as reference examples.

**Processing**: A human labels the ground truth directly on a frozen run — which articles were genuinely relevant, whether the #1 pick was truly the best, whether the rest were sensibly ordered, and (optionally) writes an ideal reference script for a segment. Every judge is then re-run fresh against that same frozen run, and where real human labels exist (relevance, ranking), the agreement between judge and human is computed as a single number.

**Output**: An agreement score per judge, and a full side-by-side view for a human to spot-check the judges that don't have a computed score.

**Consumer**: Whoever's deciding whether a judge prompt needs another pass before being trusted more broadly. This workflow never touches or blocks the live pipeline — it only ever runs against an already-completed, explicitly-picked run.

---

## How You'd Actually Interact With This

The harness ships as a small internal dashboard with four tabs:

- **Users** — manage test users/topics, and the two-phase cache view (Pre-Opt's shared cache vs. a specific user's brief).
- **Open Coding** — every past generated brief, archived, with room for a researcher's free-text notes.
- **Gold Set** — the manual calibration workflow: freeze a run, label it, run judges, see agreement scores.
- **Judge Output** — the automated judging report card: recent runs, flag rates, and (for article faithfulness) the full statement-by-statement reasoning table, including any regeneration attempts.

Brief generation itself is triggered per-user via a single API call (or the dashboard's own button) — there's no scheduler yet; "when should this run" is a deliberate non-goal of the current build.
