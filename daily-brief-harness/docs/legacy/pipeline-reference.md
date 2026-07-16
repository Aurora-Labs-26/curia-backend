# Curia Daily Brief — Pipeline Reference

## Purpose (from `constitution.md`)

Daily Brief extends Curia (an app that turns a user's saved/bookmarked links into an audio show) into a **daily** personalized news podcast. Each brief should:

- Cover the latest news on the user's **most-recently saved links**, sorted by how groundbreaking it is, capped at 3–4 stories.
- Cover the latest news relevant to the **user's location** (defaults to India if unspecified; can be as specific as a city).
- Stay under ~650 words of actual content (excluding the intro).
- **Never repeat itself** for a user — the same real-world event should only ever be covered once, but genuine follow-up developments are allowed.
- The intro is **not** part of the generated transcript — it's personalized text (`"hey <user>, it's been <N> days..., weather is <X>..."`) that gets prepended only at playback time.
- The dashboard (this repo) exists purely to test/tune the above with configurable inputs, and must be deterministic enough that re-running with identical inputs on the same day surfaces the same articles.

---

## End-to-End Flow

```
Step 1: User Context  →  Step 2: Fetch Articles  →  Step 3: Score & Curate (LLM)
   →  Step 4: Narrative Outline (LLM)  →  Step 5: Write Transcript (LLM)
        ↳ (at playback time, separately) →  Intro Generation (LLM)  →  Final stitched brief
```

Backend entry points live in [app/main.py](app/main.py); orchestration logic lives in [app/services/pipeline.py](app/services/pipeline.py) (`PipelineManager`); prompts live in [app/templates/prompts.py](app/templates/prompts.py).

---

## Step 1 — User Context

Not an API call — just the `UserProfile` shape collected from the harness UI (or from `curia-backend` in production):

```python
UserProfile: name, interests[], location, saves[], days_since_last_brief,
             unlistened_queue[], structure ("Structure 1"|"Structure 2"), voice ("Voice A"|"Voice C")
```

- `interests`: active topic chips (e.g. "AI & Machine Learning", "India Tech & Startups").
- `saves`: user's recently bookmarked links (`{title, url, summary}`).
- `structure`/`voice`: control Step 5's narrative shape and tone (see below).

---

## Step 2 — Fetch Articles

**`POST /api/news/fetch`** → `news_service.fetch_articles_for_profile()` in [app/services/news_service.py](app/services/news_service.py). No LLM call — pure RSS fetching from Google News.

For each active interest:
- Looked up in `TOPIC_CONFIGS` (topic feed like `SCIENCE`/`BUSINESS`, or a keyword search query like `"AI" OR "Claude" OR "OpenAI"`).
- Global topics (AI, Semiconductors, Big Tech, Cybersecurity, Science, Business, World) query US/English feeds; regional ones (India Tech, Politics, Sports) query India feeds when the location implies India.
- Up to **50 articles per topic** are fetched via Google News RSS, filtered to the **last 48 hours**.

Then, unconditionally:
- **Local news**: one geo-targeted RSS feed for the user's exact `location`, up to 20 articles, tagged with `topic = "Local: <location>"`.
- **Discovery**: one feed from a topic *not* already covered by the user's interests (SCIENCE/ENVIRONMENT/SPACE/HEALTH), up to 10 articles, tagged `topic = "Discovery"`.

All results are deduped by URL and normalized title, then shuffled. If fewer than 4 articles survive (e.g. network failure), a small set of hardcoded `FALLBACK_ARTICLES` is added. There is currently **no quality/allow-list filtering** at this stage (that used to exist in the disabled no-LLM `scoring_service.py` — see "Disabled code" below) — everything fetched flows into Step 3.

---

## Step 3 — Score & Curate (LLM, combined)

**`POST /api/pipeline/score`** → `PipelineManager.run_score_curate_step()`. This single LLM call replaces what used to be two separate steps (score, then curate).

**Prompt**: `SYSTEM_SCORE_CURATE_PROMPT` / `USER_SCORE_CURATE_PROMPT` in `prompts.py`.

- Input: the full article pool (capped at `MAX_ARTICLES_FOR_SCORING = 150` as a cost/safety net — the prompt itself bounds its own output regardless of pool size), the user's requested topic(s), and their location.
- The model is told to think **comparatively**: read the whole pool, mentally rank non-local articles against each other, decide the top 4 + their order *before* assigning scores, then back the ordering with numbers.
- Two scoring dimensions only (per article):
  - **Topical Depth** (1–10, ×0.40 weight): how centrally the article belongs to the requested topic(s), not just keyword overlap.
  - **Real-World Significance** (1–10, ×0.60 weight): how much the underlying event actually matters (impact/reach), independent of popularity or source prestige.
  - `composite = topicalDepth × 0.40 + realWorldSignificance × 0.60`
- Duplicate events (same announcement/launch/etc. across outlets) are collapsed to one representative article; final 4 must be informationally diverse.
- **Local articles** (`topic` starting with `"Local:"`) are handled entirely separately: never scored on Topical Depth, never in `scored_articles`/`top_5` — the model picks exactly one best local article as `local_pick` (or `null` if none exist).

**Model output JSON**:
```json
{
  "scored_articles": [ {"title", "scores": {"topicalDepth", "realWorldSignificance"}, "composite"} ],  // top 30 non-local, ranked
  "top_5": [ {"title", "reason"} ],       // exactly 4 non-local stories, #1 = lead, in editorial order
  "local_pick": {"title", "reason"} | null
}
```

**Post-processing bridge** (in `pipeline.py`, not the model): since Outline/Transcript only have 4 global segment slots (lead + 3 standard) + 1 local slot, `run_score_curate_step` maps this output into the shape everything downstream expects:
- `top_5[0..3]` → `selections` with `slot` = `"Main Story"`, `"Supporting Story 1"`, `"Supporting Story 2"`, `"Supporting Story 3"`.
- `local_pick` (if present) → `selections` entry with `slot = "Local Pulse"`.
- Any 5th `top_5` entry (there can be at most one extra) → `cuts` with a generic reason.
- Result: `parsed.selections` (list of `{title, slot, reason}`) and `parsed.cuts` are added alongside the raw `scored_articles`/`top_5`/`local_pick`, so the harness UI, history storage, and Step 4 don't need to know about the leaner model schema.

---

## Step 4 — Narrative Outline (LLM)

**`POST /api/pipeline/outline`** → `PipelineManager.run_outline_step()`. Uses `SYSTEM_OUTLINE_PROMPT` / `USER_OUTLINE_PROMPT`.

- Input: `selections` from Step 3 (already in editorial priority order), the user's interests/location, and the chosen `structure`.
- The model does **not** write narration — it designs structure only: which segment types to use, what facts each *must* cover (`required` vs `optional`), what to deliberately omit, and a word budget per segment.
- Fixed segment types: `NEWS_GLIMPSE` (Preview structure only), `NEWS_LEAD`, `NEWS_STANDARD_1/2/3` (all three mandatory), `LOCAL`, `OUTRO`. No intro segment — that's added later, separately.
- Word budget: **700–800 words total**, `LOCAL` capped at 60–70 words, lead story gets the largest share.
- Output: `{"segments": [{type, covers_articles: [indices], must_cover: [{importance, point}], omit: [...], word_budget}]}`.

---

## Step 5 — Write Transcript (LLM)

**`POST /api/pipeline/transcript`** → `PipelineManager.run_transcript_step()`. Uses `SYSTEM_TRANSCRIPT_PROMPT` / `USER_TRANSCRIPT_PROMPT`.

- Input: the Step 4 outline (treated as authoritative — must cover every "required" point, respect word budgets, preserve segment order) plus the original selected articles for factual grounding.
- **Structure** picks segment order/character:
  - *Structure 1 ("The Preview")*: Glimpse → Lead → Standards → Local → Outro.
  - *Structure 2 ("The Lead")*: Lead → Standards → Local → Outro (no glimpse).
- **Voice** picks tone:
  - *Voice A ("The Smart Friend")*: casual, contractions, short sentences.
  - *Voice C ("The Curious Companion")*: reflective, question-driven, connects ideas.
- No introduction is written here — the transcript starts directly with the first news segment.
- Output is **plain text**, not JSON, with segments wrapped in bracket tags: `[NEWS_LEAD] ... [NEWS_STANDARD_1] ... [LOCAL] ... [OUTRO]`. `pipeline.parse_transcript_segments()` regex-splits this into `{type, text, word_count, host}` objects for the UI (`parsed_segments`).
- Target: 700–800 words total, matching Step 4's budget.

---

## Intro Generation (separate, playback-time only)

**`POST /api/pipeline/intro`** → `PipelineManager.generate_intro()`. This is **never** part of the Step 3–5 transcript — per the constitution, it's dynamically generated and prepended only when the user actually presses play, so weather/time-of-day/calendar are always fresh.

Before calling the LLM, `main.py`'s endpoint enriches the request if `weather`/`local_time`/`calendar_summary` aren't already supplied:
- **Weather + local time**: [WeatherAPI.com](https://www.weatherapi.com) `current.json`, keyed by `settings.WEATHERAPI_KEY`, queried with the user's `location`. Falls back to the literal string `"pleasant"` if no key is configured or the request fails (failures are logged, not raised).
- **Calendar**: `calendar_service.get_today_summary()` — a Google Calendar service-account integration ([app/services/calendar_service.py](app/services/calendar_service.py)) that reads today's events off a calendar shared with the service account, returning a one-line summary like `"3 meetings today, starting with Standup at 9:00 AM"`. Only runs if `GOOGLE_SERVICE_ACCOUNT_FILE` is configured.

**Prompt** (`SYSTEM_INTRO_PROMPT`): casually greet the user by name, ground the greeting in their local time of day, acknowledge the gap since their last brief if >1 day, mention weather with a practical tip, optionally weave in the day's schedule — all in **35–55 words**, no headers/brackets.

The harness prepends this returned intro segment to the Step 5 transcript for the "Preview with Intro" view, but it is stored/generated independently and is excluded from Step 5's word budget and JSON.

---

## Final Assembly

The full brief a user hears = `[Intro segment (generated at playback)] + [Step 5 transcript segments, in order]`. Nothing in Steps 2–5 knows about the intro; nothing in intro generation knows about the transcript content beyond username/location/time context.

---

## Supporting Infrastructure

- **LLM execution** ([app/services/llm_service.py](app/services/llm_service.py)): all Step 3/4/5 + intro calls go through `LLMService.call_llm()`, model `claude-haiku-4-5-20251001`. Temperature `0.2` for Steps 3–4 (JSON, needs consistency), `0.7` for Step 5/intro (prose, wants some flavor). If no Anthropic key is configured (server-side or per-request header), it **automatically falls back to canned mock responses** (`MOCK_SCORE_CURATE_RESPONSE`, `MOCK_OUTLINE_RESPONSE`, `MOCK_TRANSCRIPT_A/C`) so the harness UI stays fully testable without burning API credits. Step 3 gets a larger `max_tokens` (8000 vs 4000) since it scores up to 30 articles per response.
- **History storage** ([app/services/history_service.py](app/services/history_service.py)): every completed harness run is saved (Firestore in production, local JSON files in `app/data/runs/` in dev) under keys `step_2_raw_articles`, `step_3_scored_articles`, `step_3_curated_slots`, `step_4_narrative_outline`, `step_5_transcript`, plus per-step thumbs-up/down feedback and per-article relevance ratings. `get_seen_stories(username)` reads `step_3_curated_slots` across all past runs — intended to feed the anti-repeat rule from the constitution, though the current `SYSTEM_SCORE_CURATE_PROMPT` doesn't yet re-ingest this list (a known gap, not wired up).
- **Production batch runner** ([app/services/brief_runner.py](app/services/brief_runner.py)): the real (non-harness) path. `run_batch()` is triggered by a Railway cron via `POST /internal/run-brief-batch`, fans out over all brief-enabled users (concurrency-capped at 5), and for each user runs Fetch → Score&Curate → Outline → Transcript → TTS (`tts_service`) → upload audio (`storage_service`) → write to DB (`db_service`) → FCM push notification (`push_service`). Mirrors the harness's Steps 2–5 exactly (same `pipeline.py` methods), just without the UI.

---

## Disabled / Legacy Code (kept for reference, not executing)

- [app/services/scoring_service.py](app/services/scoring_service.py): the original **no-LLM** Step 3+4 — sentence-embedding similarity against hand-written topic anchors, greedy same-story clustering, and a hard-negative-phrase + source-allowlist filter. Fully commented out; superseded by the combined LLM prompt above. Its old Layer-1 filtering (junk phrase/source blocking) has no direct replacement in the current pipeline — the LLM is trusted to make that judgment call implicitly via the significance/topical-depth rubric.
- `prompts.py`: old separate `SYSTEM_SCORE_PROMPT`/`SYSTEM_CURATE_PROMPT` (with a 4-dimension score: topic alignment, save resonance, significance, recency, and an explicit anti-repeat rule) were removed outright in favor of the current 2-dimension combined prompt. Save-resonance-based personalization and the anti-repeat rule are **not** present in the current prompt — full history predates today's design.
