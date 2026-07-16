# Daily Brief — Technical Reference

Companion to `daily_brief_high_level_summary.md`. That document explains *what* each block does and *why*; this one is the exact numbers — models, token budgets, thresholds, word caps, schema, and real measurements pulled from the live database and actual API calls, not just read from the code. Where a number was empirically verified (via a live query or test run) rather than just read from source, it's marked **[verified]**.

---

## 1. Models & Per-Step Cost Tiers

Two models, chosen deliberately per step (`app/services/llm_service.py`):

| Constant | Model | Used for |
|---|---|---|
| `ACTIVE_MODEL` | `claude-haiku-4-5-20251001` | Article segment writing (step 6), intro/outro (step 7) — live-traffic generation, cost-sensitive |
| `JUDGE_MODEL` | `claude-sonnet-5` | Score & Curate (step 3) + every judge (steps 8-13) |

**Why Score & Curate (step 3) is on the judge-tier model despite being generation, not judging**: moved 2026-07-13 after Haiku was observed live letting 7 separate wire-service pickups of one breaking event (Iran strikes moving oil/equity markets) survive as 7 separate clusters instead of collapsing to one. A same-prompt, same-data replay on Sonnet correctly collapsed all 7 — isolated as a model-capability gap, not a prompt-wording issue.

**Why judges are on a different tier than the generator**: explicit self-preference-bias mitigation (a model judging its own output tends to rate it more favorably) — judges never generate user-facing content, so the extra cost only applies to judging calls, not live traffic (except step 3, the one exception above).

### `step_id` map

| step_id | Name | Model tier | Notes |
|---|---|---|---|
| 3 | Score & Curate | JUDGE_MODEL | live generation, not a judge |
| 6 | Article segment write | ACTIVE_MODEL | |
| 7 | Intro/outro | ACTIVE_MODEL | |
| 8 | Relevance judge | JUDGE_MODEL | |
| 9 | Order-correctness + order-ranking judge | JUDGE_MODEL | one call answers both |
| 10 | Faithfulness (article) judge | JUDGE_MODEL | web search tool enabled |
| 11 | Faithfulness (bookend) judge | JUDGE_MODEL | intro+outro judged in one combined call |
| 12 | Tone/flow pairwise judge | JUDGE_MODEL | called twice per comparison (position-swap) |
| 13 | Brief coherence judge | JUDGE_MODEL | |

### `max_tokens` per step (`llm_service.py:293-323`)

| step_id | max_tokens | Why |
|---|---|---|
| 3 | 20,000 | Pool can be up to 150 articles (`MAX_ARTICLES_FOR_SCORING`); a 35-article test already hit 12,000. 20,000 is the largest safe margin under the Anthropic SDK's own non-streaming ceiling (~21,333, from its 10-minute timeout estimate) — this is a hard API ceiling, not a tuned value. |
| 8 | 16,000 | Confirmed live against a 54-article pool: 6,000 was entirely consumed by extended-thinking tokens (5,391 of them), leaving nothing for output and silently truncating to zero relevance rows. 16,000 completed cleanly (~7.4k output tokens, `end_turn`) with headroom. |
| 9 | 8,000 | Bumped preemptively (now answers 2 questions per call, not 1) given step 8's demonstrated thinking-eats-the-budget pattern. |
| 10 | 12,000 | Web search adds multiple search rounds' worth of tool-result content + reasoning before the final JSON; the generic 4,000 default isn't enough for a 4-8 claim segment with search on. |
| 11 | 6,000 | Combined intro+outro call (was two separate 4,000-budget calls) — output roughly doubled, bumped for headroom but not maxed like step 10 since bookend scripts are short and there's no web search. |
| everything else | 4,000 | default |

**Temperature**: `0.7` for `ACTIVE_MODEL` steps only. `JUDGE_MODEL` (Sonnet) rejects an explicit `temperature` kwarg outright (400 error, "deprecated for this model") — never sent for steps 3, 8-13.

**Web search tool** (step 10 only): `{"type": "web_search_20260209", "name": "web_search"}`, server-executed, no client-side tool loop needed.

**Response parsing quirks**: `JUDGE_MODEL` can return a leading `ThinkingBlock` before the real text — the code takes the *last* text block, not the first, since step 10's web-search responses interleave multiple text/tool-result blocks. `stop_reason == "max_tokens"` is logged as a likely truncation; `stop_reason == "pause_turn"` (server-side tool loop hit its cap mid-search) is logged separately since there's no multi-turn resume support.

**Simulated mode**: if no `ANTHROPIC_API_KEY` is configured (server-level or per-call), every step returns a fixed mock response instead of a real call (`~1.2s` artificial delay). Mock judge responses are all "clean" (`worst_severity: none`) — simulated mode can exercise every code path except an actual judge flag or the regen loop's retry branch.

---

## 2. Generation Pipeline — Parameters & Budgets

### 2.1 Article discovery (`app/services/news_service.py`)

- **The 7 Beats** (`BEATS` dict) and their Google News topic-vertical code(s):

  | Beat | Google topic code(s) | Geo mode |
  |---|---|---|
  | Tech | `TECHNOLOGY` | global (US edition only) |
  | Business | `BUSINESS` | mixed (US + national edition, caller dedups) |
  | World | `WORLD` | global |
  | Science & Health | `SCIENCE`, `HEALTH` (two codes) | global |
  | Culture | `ENTERTAINMENT` | mixed |
  | Lifestyle | *(no code — keyword search)* `travel, food, restaurant, recipe, fashion, "home improvement", gardening, "real estate", relationships, parenting, wellness, fitness, shopping` | national |
  | Sports | `SPORTS` | global |

- `BEAT_ARTICLE_CAP = 50` — every distinct query (a Beat's topic feed(s), a custom topic, or local) gets this budget independently; split evenly across geo-edition combos when a Beat needs more than one.
- `DEFAULT_NATIONAL_GEO` falls back to India's geo params when no country is supplied (not a silent global default).
- 22 countries have hardcoded `(gl, hl, ceid)` triples.
- RSS fetch: `urllib.request`, spoofed Chrome User-Agent, **5s timeout**.
- **Recency cutoff: 30 hours** (a stale in-code comment says "20 hours" — the actual constant is 30h; items with an unparseable pubDate are kept, not dropped).
- **No dedup at this layer** — every fetch function explicitly leaves dedup to `scoring_service`.
- Custom-topic fetch and local-news fetch both use a **20-article** cap (call-site value in `user_brief_runner.py`, not a `news_service` constant).
- Dead code confirmed inert: `IAB_COMPATIBILITY_CLUSTERS` (a commented-out 21-category taxonomy, pre-dates the 7-Beat system).

### 2.2 Local ranking / dedup (`app/services/scoring_service.py`) — no LLM call

- **Embedding model**: `all-MiniLM-L6-v2` (sentence-transformers), loaded as a singleton, `torch.set_num_threads(1)` for CPU-quota-limited containers, warmed at app startup.
- **Topic Similarity (TS)**: max cosine similarity of an article's embedding against per-Beat example-headline anchor sets (7 Beats, deliberately no named entities/companies in the anchors — measured to not generalize). Local articles get a **fixed neutral TS = 0.5** — no topic gating at this layer.
- **Dedup clustering**: greedy single-pass — `CLUSTER_SIM_THRESHOLD = 0.72`; `CLUSTER_SATURATION = 5` (cluster size at which cluster-based significance maxes out); up to `MAX_ALTERNATES_PER_ARTICLE = 5` same-story alternates carried forward per entry as a scrape-fallback list. Local articles are clustered in a separate pool, never merged with non-local.
- **Real-world Significance (RS)**: `0.5 × cluster_rs + 0.5 × heuristic_rs`, where:
  - `cluster_rs = min(cluster_size - 1, 5) / 5`
  - `heuristic_rs = 0.65 × story_type_score(title) + 0.35 × dollar_score(title)` — `story_type_score` regexes the headline (0.90 launch/unveil, 0.85 large funding, 0.80 cancel/crackdown/probe, 0.65 acquire/merger, 0.60 IPO/valuation, 0.30 stock moves, 0.20 opinion/listicle, 0.50 default); `dollar_score` = `$B` amount ÷ 5.0 (capped 1.0) or `$M` amount ÷ 500.0 (capped 0.8), else neutral 0.50.
- **Composite** = `(TS × 0.55 + RS × 0.45) × 10 + multi-topic boost`. Boost: `+0.30` per additional active-interest match above raw cosine `0.40`, capped at `+0.60` total.
- **Optional enrichment refinement** runs *before* the cut (can change who's kept, not just reorder), then a second lightweight re-clustering pass runs on the enriched text (catches near-duplicates title-only embeddings missed — a measured example went from cosine 0.55-0.72 on titles to 0.75-0.90 on scraped lead paragraphs).
- **The cut**: `DEFAULT_KEEP_FRACTION = 0.5` — top 50% of the non-local pool by composite score survives. **Always force-included regardless of score**: all local-tagged entries (post-dedup) and all custom-topic entries (no fair anchor set to score them against).
- Explicitly rejected designs (confirmed absent from code, by design): no hard source allowlist, no negative-keyword gate, no named-entity significance tier list (an earlier S/A/B company tier list was removed for baking in press-volume bias).

### 2.3 Score & Curate (`app/templates/prompts/score_curate.py`) — the editorial LLM call

Four-step process the model is instructed to follow:

1. **Event dedup + relevance gate** (non-local only): single incremental clustering pass; one representative per cluster (earliest preferred). Each representative gets a hard `valid: true/false` gate (not a weight). A **concrete-claim test** runs first — a headline naming themes without asserting a specific fact/action is `valid: false` regardless of significance. `exclude_reason` when invalid: `off_topic | opinion | newsletter | roundup | top_x | superficial`.
2. **Editorial ranking** of Step 1's valid survivors, by real-world significance (impact breadth, durability, would a wire service lead with it) — not topical relevance. Full ordering, not just top-N. Tiebreakers in order: significance → broader/longer-lasting consequences → original reporting over commentary → lineup diversity.
3. **Local story, Phase A (gate)**: passes only if BOTH genuinely about the user's actual home location AND connected to a stated topic (if any exist). Zero passing is the explicitly correct outcome, never overridden by significance.
4. **Local story, Phase B (ranking)**: full order of Phase-A survivors; top one wins the local slot.
5. **Final check**: explicitly framed as "a slip-check, not a second analysis pass" — no discarded id leaked into output, no top-4 story fails the concrete-claim test, no duplicate event, no roundup at the top.

**Output schema**: `event_clusters[]` (every non-local input article, exactly once), `ranked_order[]` (every valid representative, full order, only ranks 1-4 get a `reason`), `local_ranked_order[]` (every Phase-A survivor, only rank 1 gets a `reason`). Every item carries the exact input `id` regardless of whether it has a reason.

**Editorial slot mapping** (`app/services/pipeline.py:24`): `GLOBAL_SLOT_NAMES = ["Main Story", "Supporting Story 1", "Supporting Story 2", "Supporting Story 3"]` — `ranked_order[:4]` maps to these 4, plus `local_ranked_order[0]` (if present and titled) maps to a 5th `"Local Pulse"` slot. This bridging is done in `pipeline.py`, not the prompt.

### 2.4 Full-text enrichment (`app/services/enrichment_service.py`)

- **Toggle**: `ENRICHMENT_ENABLED` env var, defaults **on**.
- **Pipeline**: `gnewsdecoder()` resolves the Google News redirect → `requests.get()` on the real publisher URL → `trafilatura.extract()` (precision-favoring, no comments/tables) → must be `≥ MIN_USABLE_TEXT_CHARS = 200` chars or the whole thing returns `None` uniformly (no distinction between "decode failed" / "site blocked" / "too short" — caller just falls back to the RSS description).
- **A real production incident, fixed by code**: `trafilatura`/`libxml2`'s first-use global-state init isn't thread-safe — concurrent first calls from different worker threads **segfaulted the whole server**, reproducibly on the first Generate Brief call after every restart. Fixed with a forced single-threaded warm-up call at boot.
- **Concurrency**: `MAX_CONCURRENT_FETCHES = 3` — tuned *down* from 12, then 6; both earlier values OOM-killed a hosted Railway deployment (`user_brief_runner`'s peak concurrent memory is higher than Pre-Opt's, since it chains rank → enrich → a second content-fetch → multiple segment LLM calls in one request).
- **Timeouts**: `PER_ARTICLE_TIMEOUT_SECONDS = 6.0` (measured real cost: ~2.2-2.8s decode + up to ~2s fetch); outer `asyncio.wait_for` at `8.0s`; `DECODE_INTERVAL_SECONDS = 1`.
- **Word caps**: `LEAD_WORD_COUNT = 120` words (used only for re-scoring topic similarity after enrichment); `FULL_TEXT_WORD_CAP = 1500` words (used when feeding text to the segment-writing LLM).
- **Same-story fallback**: on a primary-URL failure, races every same-event cluster alternate concurrently (`asyncio.as_completed`), takes whichever succeeds first, cancels the rest — used both for pre-cut re-scoring and for the final selected-articles fetch.

### 2.5 Weather (`app/services/weather_service.py`)

- WeatherAPI.com, `httpx.AsyncClient(timeout=5)` — **5s timeout, no retries, no caching** of any kind.
- Returns `("", "")` (empty, not an error) if the API key is unset, location is empty, the response isn't 200, or any exception occurs — the caller never sees a hard failure.
- Weather string format: `f"{condition_text}, {temp_c}°C"` (e.g. `"Sunny, 28.0°C"`). `local_time` is WeatherAPI's raw `location.localtime` string (e.g. `"2026-07-06 14:32"`).

### 2.6 Article segment writing (`app/templates/prompts/article_transcript_{lead,standard,local}.py`)

Word-count budgets, identical in the prompt text and in `pipeline.SEGMENT_WORD_RANGES` (`app/services/pipeline.py:84-88`):

| Segment type | Min | Target | Max |
|---|---|---|---|
| lead | 130 | ~155 | 180 |
| standard | 90 | ~110 | 130 |
| local | 70 | ~85 | 100 |

- **No corrective retry on word count** — explicitly by design ("a much shorter, lower-stakes single-segment write; just log so an over/undershooting prompt is visible without extra LLM spend"). A count outside range is logged as a warning only.
- **[Verified against real generated data]** — this budget is regularly missed in practice (see §5.2 for the actual observed distribution; lead segments average *above* the stated max).
- Standard/local segments open with a short, varied transition acknowledgment ("So, here's another one," "Oh, and closer to home," etc. — the model is told to vary phrasing, not reuse the examples verbatim); lead has no transition (it's always first).
- All three share `ARTICLE_SEGMENT_STYLE_GUIDE` (voice rules, a banned-phrase list for anchor-speak, a "never reference a specific neighboring segment" rule since the same cached script can play next to different stories for different users, and TTS-output formatting rules: no markdown, no em dashes, nothing but literal spoken words).
- On a full-text-fetch failure (`content_fetched=False`), an explicit note is injected telling the model not to invent specifics beyond the headline/description.

### 2.7 Intro/outro (`app/templates/prompts/intro_outro.py`)

- One combined LLM call produces both `intro` and `outro` fields (a 2026-07-14 merge — `intro` used to be split into a separate "glimpse"/preview field, now one continuous opener).
- **intro**: 90-120 words. Must ground the greeting in the actual local time of day, include a weather remark with a practical tip, then flow into a light, spoiler-free preview of the day's stories.
- **outro**: 50-75 words. A warm recap + sign-off.
- Same TTS-formatting and anti-hallucination rules as segments ("must not drift from or invent details about the articles provided").
- Never cached — regenerated fresh for every brief, including a same-day "regenerate bookends" call that reuses the day's 5 articles untouched.

### 2.8 Pre-Opt specifics (`app/services/preopt_runner.py`)

- Runs the identical Step 2b/3/3b/6 functions as the live per-user path — not a separate implementation.
- Differences from the live path: `country=""` (no per-user country), `interests=[beat]` (single Beat only, no multi-interest blending), no local news, no custom topics, always `is_local=False` (never writes a "local" segment).
- First selection → `segment_type="lead"`; the rest → `"standard"`. Non-local selections capped `[:4]`.
- `_run_preopt_for_topic`'s article writes are semaphore-bounded (`asyncio.Semaphore(5)`).
- All 7 topics run **concurrently** via `asyncio.gather`, each isolated (one topic's failure is caught/logged, doesn't abort the other 6) — sequential would exceed ~1 minute and risk a reverse-proxy 502.
- A cache-pruning step (`prune_topic_segment_cache`) deletes any previously-cached lead/standard segment for that topic not in this run's fresh top-4, so old picks don't accumulate alongside new ones.
- Triggered manually (`POST /api/preopt/run` or per-topic) — no cron/scheduler.

### 2.9 Concurrency limits, summarized

| Limiter | Value | Where |
|---|---|---|
| Segment-write semaphore (Pre-Opt) | 5 | `preopt_runner.py` |
| Segment-write semaphore (per-user brief) | 5 | `user_brief_runner.py` |
| Concurrent full-text fetches | 3 | `enrichment_service.py` (tuned down twice after OOM kills on Railway) |
| Postgres pool | min 1 / max 10 | `harness_db.py` |

---

## 3. Judging Pipeline — Taxonomies & Schemas

### 3.1 Relevance judge (`relevance_judge.py`, step 8)

- Binary `hit`/`miss` per article (collapsed from an earlier 3-way `direct_hit/tangential/miss` split on 2026-07-12, to mirror the pipeline's own binary gate — this was also the noisiest source of inter-rater disagreement).
- **"hit"**: genuinely connects to a stated interest. Real-world significance alone never qualifies — "a major public-safety event... is still a 'miss' if it doesn't connect to a stated interest." Redundant coverage of an already-represented event is also `miss` except for the single most substantive article.
- Explicitly judges relevance only — not writing quality or standalone interestingness.
- Output: `{"labels": [{"id": int, "label": "hit"|"miss", "reason": str}]}`, one entry per input article id.
- Compared against human gold labels via Cohen's kappa, **per profile only** (a pooled figure across mixed profiles was found actively misleading and has no consumer).

### 3.2 Order-correctness / order-ranking judge (`order_correctness_judge.py`, step 9)

One call, two independent verdicts:
- **order_correctness** (top-pick check): is the article in the top slot genuinely the most significant in the *whole* pool? If not, which id would the judge have picked instead (`alternative_id`, null when agreeing)?
- **order_ranking** (ranks 2-N check): setting the top pick aside, are ranks 2 through a cutoff (`window_size`) in defensible relative *order*? Judges relative order within that stretch only, not a re-pick.
- **`window_size`** = `eval_gold_service.DEFAULT_LOCAL_CANDIDATES` (4) for local pools, `DEFAULT_NON_LOCAL_CANDIDATES` (8) for non-local pools — the same cutoff the gold-labeling UI caps candidate display at.
- Framed to the model as "a senior wire editor reviewing a junior editor's work"; significance defined the same way as the Score & Curate prompt (breadth of impact, durability, would a major outlet lead with it).
- Both verdicts validated against **direct human gold labels** (`eval_gold_runs.non_local_top_correct`/`local_top_correct`/`non_local_order_correct`/`local_order_correct`, all nullable booleans — `NULL` = agree by convention), not a derived signal (an earlier derived-from-pipeline-output approach was near-circular).

### 3.3 Faithfulness — article (`faithfulness_judge.py`, step 10) + the regen loop

Redesigned 2026-07-14 (see the earlier "faithfulness-judge-plan.md" for the full rationale) to check claims against the **real world via web search**, not against the one source article — the source is reference context only, not ground truth. Not flagging a claim solely for being absent from the source is explicit in the prompt.

**Four-way severity taxonomy**:

| Severity | Meaning |
|---|---|
| `critical` | Contradicted by search, or a specific fabricated detail (number/date/quote/named entity) search finds no trace of |
| `moderate` | Corroborated only by a single low-credibility source, or an inference/editorializing claim beyond what any real source states |
| `unverifiable` | Search neither confirms nor denies — plausibly just too fresh (<24h) to be re-reported yet. Never defaulted to just because publish time is missing. |
| `stylistic` | Connective tissue, never flagged, never listed |

Rollup priority: `critical > moderate > unverifiable > none`.

Output includes `verification` (`corroborated`/`contradicted`/`unverifiable`) and `source_quality` (`wire_or_major_outlet`/`multiple_independent`/`single_low_quality`/`none`) per claim, plus a `counts` block (critical/moderate/unverifiable/total_claims) built for cross-run aggregation.

**The regen loop** (`app/services/faithfulness_regen_service.py`, added 2026-07-15, scoped to `faithfulness_article` only — bookends are explicitly excluded):

- **Trigger**: `critical` or `moderate` only. `unverifiable` never triggers a rewrite — rewriting on "can't confirm yet" risks stripping true-but-not-yet-indexed content.
- **Cap**: `MAX_RETRIES = 2` (3 attempts total: 1 initial + up to 2 regenerations).
- **Regeneration input**: the prior flagged draft + the exact flagged claims (pre-formatted as a bullet list), spliced into a dedicated addendum (`USER_ARTICLE_SEGMENT_REGEN_BLOCK`) appended to the normal segment-writing prompt. Framed as "fix this correction," not "rewrite from scratch" — explicitly told to keep the same voice/pacing/length target and to drop or soften an unverifiable claim rather than invent a replacement.
- **Resolution**: if no attempt comes back clean, the **least-severe** attempt wins; ties broken toward the **latest** attempt (a later rewrite at the same severity is not assumed to be a regression).
- **Runs synchronously**, inline in `user_brief_runner._resolve_one`, before a brief is marked ready — moved out of the old post-hoc automated-judging pass specifically so a flagged claim never reaches a "ready" brief. As a result, `faithfulness_article` was removed from `eval_judges_service.AUTOMATED_JUDGE_SETS["generate_brief"]`.
- **Cache-hit segments never regenerate** — only judged once (or the memoized verdict reused), since the text is shared across users and a rewrite now wouldn't be visible to whoever already received it. This also means the regen loop only ever fires on the *first* generation of a given article+segment_type — a segment cached before this feature shipped keeps whatever verdict it got back then until something forces a fresh cache miss.
- **Toggle**: `faithfulness_regen_enabled` (default on) governs only the rewrite; judging itself always runs synchronously either way.
- **Memoization**: the winning verdict is written to `article_segment_cache.faithfulness_severity`/`faithfulness_detail`, computed once per unique cached text, not once per user who happens to receive it.
- **Attempt tracking**: `eval_judge_outputs.attempt_number` (default 1) — the dashboard only shows `"(attempt N)"` in a segment's header when more than one attempt actually happened for it; the common single-attempt case is unchanged.

### 3.4 Faithfulness — bookend (`faithfulness_judge.py`, step 11)

- Checked against **structured inputs** (name/location/weather/local time/story picks), not the real world — there's nothing to web-search a listener's name against.
- Reuses the simpler 3-level taxonomy (`critical`/`moderate`/`stylistic`) shared with the pre-redesign article judge.
- One combined call judges intro and outro independently (merged 2026-07-14 — they always come from the same generation call and inputs, so nothing is lost combining them; still logs two separate `eval_judge_outputs` rows).
- **Never regenerates** — a recurring flag here is treated as a prompt bug to fix by hand, not something to auto-patch.

### 3.5 Tone/flow pairwise + brief coherence (`tone_flow_judge.py`, steps 12-13)

- **Pairwise**: anonymized "Script A"/"Script B" comparison against a hand-authored golden reference, judged on tone/naturalness only (not content, length, or topic). **Called twice with the scripts swapped** — if the winner flips depending on slot, the verdict is forced to `"inconclusive"` rather than trusting either single call (position-bias control). Cost is the sum of both calls.
- **Brief coherence**: whole stitched brief (intro + every segment + outro, in broadcast order), rated 1-5 on transition smoothness, with anchor definitions at 5 ("flows as one continuous, natural listen"), 3 ("acceptable but noticeably episodic"), 1 ("jarring... a real listener would notice"). Also returns the single weakest transition (or null).
- Neither has dedicated gold labels — validated via human spot-check in the dashboard, not a computed kappa.

### 3.6 Deterministic checks (`app/services/eval_checks_service.py`) — zero LLM cost

| Check | Logic | Result |
|---|---|---|
| TTS markdown artifacts | regex for links/bold/italic/headers/backticks/raw URLs | fail if found |
| TTS normalization flags | regex for unexpanded `$` amounts, `Q[1-4]`, 2-6 letter acronyms | flag only (often legitimate) |
| Empty/error leak | empty string or one of 10 hardcoded LLM-refusal phrases | fail |
| Segment word budget | actual vs. `SEGMENT_WORD_RANGES`, **±10% tolerance** (`_WORD_BUDGET_TOLERANCE = 0.10`) | flag |
| Total duration band | word count at 150 wpm vs. **300-360s** target band | flag |
| Structure counts | exactly 1 lead, ≤3 standard, ≤1 local | fail |
| Personalization: name/location | substring match in intro | fail |
| Personalization: weather | condition word (>3 chars) or temp integer in intro | flag |
| Personalization: time greeting | HH parsed into morning(5-12)/afternoon(12-17)/evening(17-22)/night, keyword match | flag |
| Ops: run completed | `status == "completed"` | fail otherwise |
| Ops: cost summary | sums latency/tokens across the run | always pass, informational |

Never blocks anything — `run_checks_for_run()` catches its own exceptions. Run-kind-aware: a `preopt` run skips personalization/duration checks (no bookends); a `regenerate_bookends` run skips segment/structure/duration checks (no fresh segments that run).

### 3.7 Automated judging orchestration (`app/services/automated_judging.py`, `eval_judges_service.py`)

- `AUTOMATED_JUDGE_SETS`:
  - `"generate_brief"` → `["relevance", "order_correctness", "order_ranking", "faithfulness_bookend"]` (faithfulness_article removed 2026-07-15 — now synchronous/inline, see §3.3)
  - `"regenerate_bookends"` → `["faithfulness_bookend"]` only (the 5 articles/segments are reused untouched — nothing else is genuinely fresh)
  - `preopt` is not in this dict at all — no user-facing brief, out of scope
- Fires as a genuine fire-and-forget `asyncio.create_task`, **after** the brief's response is already fully built — a judging failure can never affect or delay a brief that already succeeded. Task references are held in a module-level set with a done-callback, avoiding the classic "unreferenced task gets garbage-collected mid-flight" asyncio footgun.
- Gated by `settings_service.is_automated_judging_enabled()` — checked inside the fire-and-forget task itself, so even the toggle-check failure degrades to "skip this once," never an unhandled exception in the background.
- `run_automated_judges` deletes prior judge-output rows for a run **scoped to the judge names it's about to recompute** (`delete_judge_outputs_for_run(run_id, judge_names=...)`) — a real bug found and fixed 2026-07-15: the original blanket delete would have wiped the synchronous faithfulness_article regen loop's multi-attempt rows the moment this background pass fired, since it runs after those rows are already logged.

### 3.8 Gold-set calibration mechanics (`eval_gold_service.py`)

- **Freezing**: pins one `eval_runs.id` as a labeled reference profile (`eval_gold_runs`, `UNIQUE(run_id)`). `unfreeze` soft-disables (`is_active=false`); a hard-delete trigger that used to block editing a frozen run's underlying data was removed (migration 008) — freezing is now a purely informational marker, not a DB-enforced lock.
- **Relevance labels**: append-only, supersede-on-edit (`superseded_at`), one active label per `(run_id, url)` enforced by a partial unique index.
- **Order labels**: 4 nullable booleans directly on `eval_gold_runs` (`non_local_top_correct`, `local_top_correct`, `non_local_order_correct`, `local_order_correct`) — `NULL` = unmarked = agree, by convention.
- **Golden scripts**: same append-only/supersede pattern, keyed by `(run_id, segment_type, article_url)` — `article_url` is `NULL` for intro/outro.
- **`DEFAULT_NON_LOCAL_CANDIDATES = 8`**, **`DEFAULT_LOCAL_CANDIDATES = 4`** — display defaults for the labeling UI (not enforced limits), reused directly as the order-correctness judge's `window_size`.
- Cohen's kappa (`sklearn.metrics.cohen_kappa_score`) computed only for relevance and order-correctness/ranking (the only judges with real human gold labels), **per profile_label**, never pooled across profiles.

---

## 4. Database Schema Reference

Schema `harness`, 20 tables, built across 16 migrations (`db/001_schema.sql` → `db/016_faithfulness_regen.sql`). Grouped below by concern; every table's *final* shape after all migrations.

### Enum types

| Enum | Final values | Notes |
|---|---|---|
| `harness.user_topic_type` | `chosen`, `custom` | |
| `harness.segment_type` | `lead`, `standard`, `local` | Eval/gold tables often store this as free `text` instead, so they can also represent `intro`/`outro` |
| `harness.brief_status` | `generating`, `ready`, `failed` | |
| `harness.eval_run_kind` | `preopt`, `generate_brief`, `regenerate_bookends` | |
| `harness.eval_run_status` | `running`, `completed`, `failed` | |
| `harness.eval_relevance_label` | `hit`, `miss` | Originally 3-way (`direct_hit`/`tangential`/`miss`), collapsed by migration 011 |

### Group 1 — Core app data

| Table | Purpose | Key columns |
|---|---|---|
| `users` | One row per listener | `email` (unique), `location_name`, `scheduled_time` (default `07:00`, unused — no scheduler yet) |
| `topics` | A subject: one of 7 seeded Beats or a free-typed custom topic | `beat` (non-null only for the 7 system rows), `is_system`; unique on `(lower(name), is_system)` |
| `user_topics` | A user's subscription to a topic | `type` (`chosen`/`custom`); `UNIQUE(user_id, topic_id)` |
| `articles` | One row per unique real-world article, deduped by `normalized_url` across every discovery context | `normalized_url` (unique — the entire cross-context cache-hit mechanism); `topic_id` nullable (local articles have none) |

### Group 2 — Brief output

| Table | Purpose | Key columns |
|---|---|---|
| `article_segment_cache` | One row per generated segment script, shared across every user | `UNIQUE(article_id, segment_type, is_local)`; `faithfulness_severity`/`faithfulness_detail` (added migration 014, nulled on any real text change); `mp3_url`/`duration_s` are placeholders — no real TTS this iteration |
| `daily_briefs` | One brief per (user, date) | `status` enum; `UNIQUE(user_id, date)` |
| `daily_brief_articles` | One row per article slotted into a brief, at a position | `rank` disambiguates standard-1/2/3; `reason` persisted so a same-day bookend-only regen can rebuild input without re-ranking; `UNIQUE(brief_id, segment_type, rank)` |
| `transcript_records` | Immutable archive of every completed generation, for the Open Coding dashboard tab | Denormalized snapshot (display_name/topics/segments captured at generation time, not live-joined) — a later user/topic change never alters a past record |

### Group 3 — Eval / judging logs (write-only from the pipeline's perspective; never read by production generation)

| Table | Purpose |
|---|---|
| `eval_runs` | One row per pipeline invocation being logged (`kind`: preopt / generate_brief / regenerate_bookends) |
| `eval_fetched_articles` | Immutable per-run snapshot of every raw article seen, before ranking — the ground truth pool for faithfulness checks; `full_text`/`content_fetched` backfilled after content-fetch |
| `eval_ranking_output` | Step 2b's full scored breakdown per article (computed every run, mostly unused downstream) |
| `eval_llm_ranking_output` | Step 3's full `ranked_order`/`local_ranked_order` including editorial `reason` text — the order-correctness ground truth |
| `eval_segment_transcripts` | Append-only log of every segment produced/reused per run (cache hits logged too, without latency/token/model) |
| `eval_meta_segments` | One row per run for the intro/outro pair, plus `inputs_used` snapshot |
| `eval_results` | Deterministic-check results (`eval_checks_service` output) |
| `eval_judge_outputs` | One generic table for every judge's verdict; `detail jsonb` holds judge-specific payload; `attempt_number` (migration 016) for the regen loop |

*A `trg_block_frozen_mutation` trigger (added migration 007) that blocked editing rows tied to a frozen gold run was removed in migration 008 — deemed too coarse, since no gold label/script is actually keyed off that data.*

### Group 4 — Gold-set calibration

| Table | Purpose |
|---|---|
| `eval_gold_runs` | Pins one `eval_runs.id` as a reference profile; carries the 4 nullable top/order-correctness booleans |
| `eval_gold_labels` | Human hit/miss label per (run, article), append-only/supersede pattern |
| `eval_gold_scripts` | Hand-authored ideal script per (run, segment), same append-only pattern |

### Group 5 — Settings

| Table | Purpose |
|---|---|
| `eval_settings` | Singleton row (`id` CHECK-constrained to always `true`); `automated_judging_enabled`, `faithfulness_regen_enabled` — both default `true`, persisted in Postgres so a redeploy never silently resets them |

---

## 5. Real Observed Data

Pulled directly from the local database and real (non-simulated) API calls — not inferred from code alone.

### 5.1 Table sizes **[verified, local dev DB]**

Row counts below are from Postgres's own statistics view (`pg_stat_user_tables.n_live_tup`), which is periodically refreshed by autovacuum rather than always exactly current — treat these as approximate scale, not a live count. (Spot-checked one table directly: `topics` showed 34 in the stats view but 69 on a direct `SELECT COUNT(*)` at query time — the 7 seeded system Beats plus 62 user-added custom topics.)

| Table | Approx. rows |
|---|---|
| `eval_fetched_articles` | 1,500 |
| `eval_ranking_output` | 1,379 |
| `eval_results` | 984 |
| `eval_llm_ranking_output` | 578 |
| `articles` | 525 |
| `eval_judge_outputs` | 391 |
| `eval_segment_transcripts` | 152 |
| `article_segment_cache` | 110 |
| `daily_brief_articles` | 68 |
| `user_topics` | 64 |
| `transcript_records` | 60 |
| `eval_runs` | 39 |
| `topics` | 34 (69 by direct count — see note above) |
| `users` | 19 |
| `daily_briefs` | 16 (all currently `ready`) |
| `eval_gold_runs` | 3 |

### 5.2 Word-count reality vs. the stated budget **[verified]**

| Segment type | Prompt target | Prompt max | **Actual avg (n)** | **Actual min–max** |
|---|---|---|---|---|
| lead | 155 | 180 | **197.2** (n=27) | 138–**236** |
| standard | 110 | 130 | **137.5** (n=71) | 75–205 |
| local | 85 | 100 | **91.5** (n=12) | 67–112 |

Lead and standard segments regularly exceed their hard-stated maximum in practice — consistent with the deliberate "no corrective retry" design (§2.6): the budget is enforced by prompt instruction and logged-on-violation only, not mechanically retried.

### 5.3 Faithfulness verdict distribution **[verified]**

`article_segment_cache.faithfulness_severity` across all 110 cached segments: 46 never judged (null — mostly pre-dating the automated-judging feature), 24 `none`, 22 `moderate`, 17 `critical`, 1 `unverifiable`. **Of the 64 that have been judged, 60.9% were flagged moderate or critical.**

`eval_judge_outputs` verdict counts (cumulative, across all runs):

| Judge | Verdict/severity | Count |
|---|---|---|
| faithfulness_article | clean | 30 |
| faithfulness_article | flagged / moderate | 20 |
| faithfulness_article | flagged / critical | 17 |
| faithfulness_article | flagged / unverifiable | 2 |
| faithfulness_bookend | clean | 22 |
| faithfulness_bookend | flagged / moderate | 11 |
| faithfulness_bookend | flagged / critical | 1 |
| relevance | hit | 193 |
| relevance | miss | 41 |
| order_correctness | agree | 23 |
| order_correctness | disagree | 3 |
| order_ranking | agree | 17 |
| order_ranking | disagree | 9 |

Derived rates: relevance judge hit rate **82.5%**; order-correctness (top-pick) agreement **88.5%**; order-ranking (ranks 2-N) agreement **65.4%** — notably lower, suggesting the pipeline's *relative* ordering below the top pick is less reliable than its top-pick choice.

### 5.4 Real token/latency benchmarks **[verified, real API calls]**

Article segment generation (step 6, `claude-haiku-4-5-20251001`, n=138 real calls):

| Metric | Value |
|---|---|
| Avg input tokens | 2,331 |
| Avg output tokens | 202 |
| Avg latency | 4,020 ms |

Intro/outro generation (step 7, n = real non-simulated rows):

| Metric | Value |
|---|---|
| Avg intro length | 97 words |
| Avg outro length | 49 words |
| Avg input tokens | 1,372 |
| Avg output tokens | 225 |
| Avg latency | 4,107 ms |

Judge calls (`claude-sonnet-5`, all real, non-simulated):

| Judge | n | Avg in | Avg out | Avg latency |
|---|---|---|---|---|
| faithfulness_article | 69 | 42,751 | 1,701 | **54,344 ms** |
| faithfulness_bookend | 31 | 1,345 | 373 | 5,934 ms |
| order_correctness | 26 | 1,948 | 726 | 10,366 ms |
| relevance | 26 | 1,958 | 1,133 | 12,742 ms |
| brief_coherence | 1 | 1,944 | 176 | 5,334 ms |

**The faithfulness_article judge is by far the most expensive single call in the entire pipeline** — ~42.7k input tokens (the web search results dominate this) and ~54 seconds average latency. A segment needing 2 retries is 3 sequential judge calls at this cost, i.e. potentially 2-3 minutes just for judging on top of 3 generation calls — the primary source of the Railway gateway-timeout risk noted in §7.

### 5.5 Run history **[verified]**

`eval_runs` by kind/status: 21 `preopt` (completed), 16 `generate_brief` (completed) + 1 (failed), 1 `regenerate_bookends` (completed).

### 5.6 Live fabrication test **[verified, real API call, see conversation history]**

A hand-fabricated false statistic + fake attributed quote, injected into an otherwise-genuine real-article segment, was correctly caught by the faithfulness judge (`severity: critical`) — the judge's web search independently identified the *actual* real researcher's name, not just "no match found." Feeding that exact flagged claim back into a regeneration call produced a clean rewrite (re-judged `severity: none`, zero claims) on the very next attempt — a live, non-mocked confirmation that the regen loop's core mechanism works end to end.

---

## 6. API Surface (`app/main.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status` | Health check |
| POST | `/api/preopt/run` | Run Pre-Opt for all 7 Beats |
| POST | `/api/preopt/run/{topic_id}` | Run Pre-Opt for one Beat |
| POST | `/api/users/{user_id}/generate-brief` | Trigger a brief for one user/date |
| POST | `/api/users/{user_id}/reset-brief` | Clear a user's brief for re-generation |
| GET/POST | `/api/harness/users` | List / create test users |
| GET | `/api/harness/topics` | List system + custom topics |
| POST | `/api/harness/clear-cache` | Wipe the article/segment cache |
| GET | `/api/harness/article-cache` | Inspect cached article segments |
| POST | `/api/harness/clear-users` | Wipe test users |
| GET | `/api/harness/transcripts` | Open Coding tab list |
| POST/DELETE | `/api/harness/transcripts/{id}/note` | Researcher notes on an archived transcript |
| GET | `/api/harness/briefs/{brief_id}` | One brief's full detail |
| GET | `/api/eval/runs/recent` | Recent eval runs |
| GET/POST/DELETE | `/api/eval/gold/runs*` | Freeze/unfreeze/delete/label gold runs (calibration workflow) |
| POST | `/api/eval/judges/run/{run_id}` | Manually run every judge against a frozen run |
| GET | `/api/eval/judges/results/{run_id}` | Judge results for one run |
| GET | `/api/eval/judges/validation-report` | Cohen's kappa report |
| GET/POST | `/api/eval/settings/automated-judging` | Automated judging on/off toggle |
| GET/POST | `/api/eval/settings/faithfulness-regen` | Regen-on-flag on/off toggle |
| GET | `/api/eval/judges/automated/recent` | Judge Output tab run list |
| GET | `/api/eval/judges/automated/rollup` | Judge Output tab trend rollup |

---

## 7. Known Constraints (not bugs — deliberate scope limits)

- **No real text-to-speech.** Every `mp3_url` is a `placeholder://...` string; `duration_s` is estimated at a fixed **150 words/minute**, never measured. Audio never actually gets synthesized this iteration.
- **No scheduler.** `users.scheduled_time` exists in the schema but nothing reads it — every brief is triggered manually via API/dashboard.
- **Railway gateway-timeout risk** is a recurring, explicitly-documented concern across multiple modules (Pre-Opt per-topic vs. bulk, segment-generation concurrency, and now the faithfulness regen loop) — a hosted reverse-proxy in front of the app has a request timeout, and several design decisions (concurrent Pre-Opt topics, semaphore-bounded segment generation, per-topic vs. bulk endpoints) exist specifically to stay under it. The regen loop is the newest and most expensive source of this risk (§5.4).
- **Segment cache is shared across all users.** A cached segment script, once generated, is replayed verbatim to every future user who selects that same article — segments are written with an explicit rule never to reference specific neighboring content, since what plays before/after a cached segment is different for every listener.
