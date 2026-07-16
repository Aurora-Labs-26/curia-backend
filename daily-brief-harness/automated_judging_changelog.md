# Automated Judging — Changelog

Personal reference for what actually got built for the automated-judging feature (see `judging_pipeline_scope_analysis.md` for why this exists at all, and `automated_judging_build_plan.md` for the milestone plan this followed). Every entry below: **why** it was added, **what** it does, **what breaks or doesn't exist without it**. Written for you to come back to later, not as a team-facing doc — assumes you already have the two planning docs' context.

Built and verified live end-to-end on 2026-07-13, all 6 milestones, against the real local dev DB and a running server (not just written and assumed correct — see "Verification performed" at the bottom for exactly what was exercised).

---

## Database migrations

### `db/013_judge_cost_logging.sql`
**Why**: `harness.eval_judge_outputs` never captured token/latency data, unlike segment/meta-segment generation logging, which does — the only way to see real judge spend was a one-off diagnostic call reading `response.usage` directly (a known gap flagged in `progress.md`). Needed before automated judging starts running unconditionally on every brief, not after, so the first week of real usage doesn't produce a cost surprise.
**What**: Adds `input_tokens`, `output_tokens`, `latency_ms` (all nullable `int`) to `harness.eval_judge_outputs`.
**Without it**: The cost-threading work in `eval_judges_service.py`/`eval_logging_service.py` (below) would have nowhere to write to — those columns are what everything else in Milestone 1 depends on.

### `db/014_faithfulness_memoization.sql`
**Why**: `harness.article_segment_cache` is shared across every user (the entire point of Pre-Opt's cache). Without a memoized verdict, the automated path would run a fresh Sonnet `faithfulness_article` call on the *exact same segment text* every time a different user happened to receive that same cached article — pure repeated spend on identical text.
**What**: Adds `faithfulness_severity` (nullable `text`) and `faithfulness_detail` (nullable `jsonb`) to `harness.article_segment_cache`.
**Without it**: Milestone 4's memoization (below) has nowhere to store or read a cached verdict — every automated faithfulness check would hit the LLM every time, regardless of how many other users already got that same article checked.

### `db/015_judging_settings.sql`
**Why**: You asked for a global on/off toggle for the whole feature, persisted (not an in-memory flag that silently resets on a server restart), and the app's own convention is to keep real state in Postgres, not process memory.
**What**: A singleton settings table (`harness.eval_settings`, one row, `id boolean PRIMARY KEY DEFAULT true` with a `CHECK (id = true)` so a second row can never be inserted) with `automated_judging_enabled boolean NOT NULL DEFAULT true`. Seeded to `true` on creation.
**Without it**: There would be no durable place for the toggle to live — either no toggle at all, or one that forgets its state every time the server restarts, which defeats the point of a deliberate kill switch.

---

## Backend — shared judge infrastructure

### `app/services/eval_logging_service.py` — `log_judge_output` gets cost kwargs
**Why**: Every judge function already gets `input_tokens`/`output_tokens`/`latency_ms` back from `llm_service.call_llm` — it was just being discarded. Threading it through is what makes `db/013`'s new columns actually get populated.
**What**: Added three new optional kwargs (`input_tokens`, `output_tokens`, `latency_ms`) to `log_judge_output`, inserted into the three new columns. Backward compatible — every existing call site that doesn't pass them still works (`None` written, same as before this column existed).
**Without it**: The new columns from `db/013` would exist but always be `NULL` — no real cost data anywhere, even though every judge call already has it available.

### `app/services/eval_logging_service.py` — `get_run_for_judges` now also selects `s.article_id`
**Why**: Milestone 4's memoization needs to look up `harness.article_segment_cache` by `(article_id, segment_type, is_local)` for each segment being judged. The existing query joined through `harness.articles` for `url`/`title` but never surfaced the raw `article_id` itself.
**What**: One additive column added to the segments sub-query (`s.article_id`) — nothing removed, nothing changed for existing consumers of this function (the manual Gold Set path ignores the new field entirely).
**Without it**: The automated path's faithfulness memoization check would have no article identity to look up the cache row with — it would either have to re-resolve it via a second query per segment, or memoization silently couldn't work at all.

### `app/services/cache_service.py` — `get_cached_segment`/`put_cached_segment`/new `set_cached_segment_faithfulness`
**Why**: The memoized verdict needs to live on the same row the segment text itself is cached on, and needs to be correctly invalidated if that text is ever genuinely regenerated (e.g. after a prompt edit + cache clear) — a stale faithfulness verdict surviving a text change would be actively misleading, worse than no memoization at all.
**What**:
- `get_cached_segment` now also selects and returns `faithfulness_severity`/`faithfulness_detail` (JSON-parsed) alongside the existing fields.
- `put_cached_segment`'s `ON CONFLICT ... DO UPDATE` now also sets `faithfulness_severity = NULL, faithfulness_detail = NULL` whenever a segment's `transcript_json` is genuinely overwritten — so a text change always invalidates any prior memoized verdict, no stale data possible.
- New function `set_cached_segment_faithfulness(article_id, segment_type, is_local, severity, detail)` — writes the memoized verdict after the automated path computes one for the first time.
**Without it**: Either no memoization at all, or (worse) a memoization that could silently serve a faithfulness verdict computed against text that no longer exists, if a segment were ever regenerated.

### `app/services/eval_judges_service.py` — the big refactor
**Why**: The manual Gold Set "Run Judges" button and the new automated path need to share the actual judge-calling logic (so they can never drift apart), but the automated path only wants a subset of judges, gated differently by run kind. The original `run_judges_for_gold_run` was one large function with all 8 judges' logic inlined — no way to call a subset without duplicating code.
**What**:
- Every `judge_*` function (`judge_relevance`, `judge_order_correctness`, `judge_faithfulness_article`, `judge_faithfulness_bookend`, `judge_overview_fidelity`, `judge_tone_flow_pairwise`, `judge_brief_coherence`) now also returns its call's `input_tokens`/`output_tokens`/`latency_ms` (summed across both calls for `judge_tone_flow_pairwise`, which makes two). `judge_relevance`'s return type changed from a bare list to `{"results": [...], "cost": {...}}` since one call scores many articles — cost is call-level, not per-article.
- The original orchestrator body was split into small per-concern helpers (`_judge_relevance_for_pool`, `_judge_order_for_pool`, `_judge_diversity`, `_judge_faithfulness_article_segments`, `_judge_tone_flow_segments`, `_judge_bookend_faithfulness`, `_judge_bookend_tone_flow`, `_judge_overview_fidelity_check`, `_judge_coherence`), each responsible for one judge's log-and-track logic. Where a single LLM call produces multiple logged rows (relevance's per-article loop; order_correctness+order_ranking sharing one call), cost is attributed to only the *first* logged row from that call, `None` for the rest — avoids double-counting if you ever `SUM()` cost per run.
- `run_judges_for_gold_run` now just calls these helpers in the exact same sequence as the original inlined code — same 8 judges, same order, same fresh-every-time (no memoization) behavior.
- New `AUTOMATED_JUDGE_SETS` dict: `{"generate_brief": [relevance, order_correctness, order_ranking, faithfulness_article, faithfulness_bookend], "regenerate_bookends": [faithfulness_bookend]}`.
- New `run_automated_judges(run_id, kind)` — calls only the helpers `AUTOMATED_JUDGE_SETS[kind]` names, with `_judge_faithfulness_article_segments` called with `use_memoization=True` (the manual path always passes `False`).
- New `list_recent_automated_runs`/`compute_automated_judging_rollup` — read paths for the Judge Output tab, deliberately never joined against `eval_gold_runs` so non-gold automated runs show up.
**Without it**: There'd be no way to run a smaller judge set without either duplicating ~200 lines of judge-calling logic into a second function (real risk of the two paths silently drifting apart over time as one gets edited and the other doesn't) or bolting a "which judges to skip" parameter onto the original function (which the build plan explicitly ruled out, to guarantee the manual button's behavior can never accidentally change).

---

## Backend — the automated trigger itself

### `app/services/settings_service.py` (new file)
**Why**: The toggle needed its own tiny, single-purpose module — not folded into `eval_judges_service.py` or `cache_service.py` — because the whole point (per your explicit requirement) is that automated judging is one self-contained block with one kill switch, not logic woven through existing services.
**What**: Two functions, `is_automated_judging_enabled()` and `set_automated_judging_enabled(bool)`, reading/writing the single row in `harness.eval_settings`.
**Without it**: The toggle's read/write logic would have to live somewhere else — most likely `eval_judges_service.py`, which would immediately violate the "one bolt-on block" requirement by making the shared judge-implementation module also responsible for the automated-only toggle.

### `app/services/automated_judging.py` (new file)
**Why**: This is *the* bolt-on block — the single thing `user_brief_runner.py` is allowed to know about. It owns two responsibilities that specifically must not live in `eval_judges_service.py`: the toggle check, and error containment for a background task that has no caller left to hand an exception to once brief generation has already returned its response.
**What**: One public function, `maybe_run_automated_judging(run_id, kind)` — checks `run_id` isn't empty, then `asyncio.create_task`s a gated inner coroutine that (a) checks the toggle, (b) if on, calls `eval_judges_service.run_automated_judges` inside a try/except that only logs on failure, never raises. The created task's reference is held in a module-level `_INFLIGHT_TASKS` set (added on creation, discarded via a `done_callback`) — a bare `asyncio.create_task()` with nothing holding the returned `Task` object can get garbage-collected mid-flight before it finishes, a real asyncio footgun, not a hypothetical one.
**Without it**: Either no background trigger at all, or one built directly into `user_brief_runner.py` with inline toggle-checking and try/except — which is exactly the "woven into the existing pipeline" shape you explicitly ruled out, and which would also risk a genuinely dropped background task if the `create_task` reference weren't held somewhere.

### `app/services/user_brief_runner.py` — one line added to each success path
**Why**: This is the actual trigger point — the moment a brief has genuinely finished generating.
**What**: `automated_judging.maybe_run_automated_judging(run_id, "generate_brief")` at the end of `generate_brief_for_user`'s success path (after `eval_checks_service.run_checks_for_run`, before the `return`), and the same call with `"regenerate_bookends"` at the end of `_regenerate_bookends_for_brief`'s success path. Both fire *after* the response dict is fully built but the function hasn't returned yet — since the call itself is fire-and-forget (`asyncio.create_task`, not awaited), the actual HTTP response is not delayed by it either way.
**Without it**: Nothing about this feature would ever run — this is the only place in the entire live pipeline that knows this feature exists, by design.

### `app/main.py` — four new endpoints + one import
**Why**: The Judge Output tab and the toggle need a way to talk to the backend.
**What**:
- `GET /api/eval/settings/automated-judging` / `POST /api/eval/settings/automated-judging` — read/write the toggle.
- `GET /api/eval/judges/automated/recent` — list of recently auto-judged runs (`eval_judges_service.list_recent_automated_runs`).
- `GET /api/eval/judges/automated/rollup` — counts by judge/verdict/severity over the last N days (`eval_judges_service.compute_automated_judging_rollup`).
- Added `settings_service` to the existing `from app.services import ...` line.
**Without it**: The frontend would have no API surface to read the toggle state, flip it, or list/summarize automated judge output — Milestone 6's UI would have nothing to call.

---

## Frontend — the "Judge Output" tab

### `static/index.html` — new tab button + tab view
**Why**: A dedicated place to see automated judge output, separate from Gold Set's manual-calibration UI (which is structurally built around one selected frozen run and can't accommodate a cross-run rollup) and separate from Open Coding (whose backing table, `transcript_records`, has no `run_id` column at all, so it can't be joined to judge output without a schema change).
**What**: A new `<button class="tab-link" data-tab="judgeoutput">` in the tab bar, and a new `<div id="view-judgeoutput">` reusing the `.goldset-grid` two-panel layout: left panel has the on/off toggle (reusing the pre-existing but previously-unused `.step2b-toggle` CSS component), a rollup summary section, and a run list; right panel is the selected run's detail.
**Without it**: No tab exists to switch to — the backend endpoints above would be unreachable from the UI.

### `static/app.js` — tab wiring + five new render/data functions
**Why**: Needs its own state and rendering, separate from `state.goldSet` and its renderers, since those assume a single frozen run with gold labels to compare against — automated runs never have gold labels.
**What**:
- New `state.judgeOutput = { runs: [], selectedRunId: null }`.
- Tab-switch handling for `"judgeoutput"` (loads data lazily on first visit, matching how Gold Set already works).
- `loadJudgeOutputTab()` — fetches toggle state, rollup, and recent runs in parallel.
- `toggleAutomatedJudging()` — POSTs the toggle, reverts the checkbox on failure.
- `renderJudgeOutputRollup(rollup)` — one small card per judge, bullet list of verdict/severity counts, critical highlighted in red with a warning icon, moderate in grey — never a bare number wall.
- `renderJudgeOutputRunList()` / `selectJudgeOutputRun()` / `loadJudgeOutputRunDetail()` — the run list and click-to-select-a-run flow, reusing `.goldset-run-card` styling.
- `renderJudgeOutputDetail(items, runMeta)` — the per-run bulleted breakdown (Relevance / Top Pick & Order Ranking / Faithfulness — Article Segments / Faithfulness — Intro/Outro), pool identity ("Non-Local"/"Local") named once per section heading rather than repeated on every row, judge names relabeled to plain English via `JUDGEOUTPUT_JUDGE_LABELS` rather than showing raw `judge_name` strings.
**Without it**: The new tab would render as empty HTML with no behavior — nothing would ever fetch or display data.

### `static/styles.css` — new rules for rollup cards, flag badges, bulleted detail view
**Why**: The rollup cards, critical/moderate badges on run-list cards, and the bulleted detail sections needed their own layout rules — `.goldset-*` classes cover the run-card/section-heading/severity-icon/claims-list pieces reused directly, but not the grid layout or card chrome specific to this tab.
**What**: `.judgeoutput-list-panel`/`.judgeoutput-detail-panel` (grid-area assignment for the reused `.goldset-grid`), `.judgeoutput-rollup-grid`/`-card`/`-card-title`/`-card-total`/`-list`/`-critical`, `.judgeoutput-flag-badge` (+`.is-critical`/`.is-moderate`), `.judgeoutput-subheading`, `.judgeoutput-bullet-list`.
**Without it**: The tab would render with correct data but broken/default layout — no card grid, no severity color-coding, no badge styling.

---

## Verification performed (not just written — actually run)

- **Manual Gold Set "Run Judges" button, unaffected**: re-ran it for real against a live frozen profile ("Product Guy") post-refactor. Identical judge-name shape to before (`relevance` ×2, `order_correctness` ×2, `order_ranking` ×2, `diversity_top4_similarity` ×1, `faithfulness_article` ×5, `faithfulness_bookend` ×2, `overview_fidelity` ×1, `brief_coherence` ×1 — 16 total), kappa validation-report endpoint still computes correctly.
- **Cost threading**: confirmed real `input_tokens`/`output_tokens`/`latency_ms` values landed in the DB, with the designed first-row-only attribution for shared-call judges (`order_ranking`/most `relevance` rows correctly `NULL`, one row per pool carrying the real number).
- **Automated trigger**: real `generate-brief` call for a throwaway user returned in ~19s (normal latency, not delayed by judging); background pass completed ~15-25s later with exactly the expected judge set for a full run (`relevance`, `order_correctness`, `order_ranking`, `faithfulness_article`, `faithfulness_bookend` — correctly scoped to only the pools that actually had candidates).
- **Kind-gating**: verified `AUTOMATED_JUDGE_SETS` structure directly — `regenerate_bookends` maps to `faithfulness_bookend` only.
- **Memoization, both directions**: confirmed a real verdict got written to `article_segment_cache` after the automated pass; then called the helper again directly with deliberately wrong stub text for that same article and confirmed it returned the cached verdict in 0.06s (vs. 6-10s for a real call) without ever touching the LLM.
- **Toggle**: flipped off via API, ran a real `generate-brief` call, confirmed zero judge output and zero `automated_judging` log activity for that run (not just empty output — genuinely never attempted). Flipped back on, confirmed a subsequent run judged normally. Also verified the real browser click path (slider click → checkbox state → API call → label update) with zero console errors.
- **Judge Output tab, live in a browser**: rollup cards, severity highlighting, run list with critical/moderate badges, and per-run bulleted detail all render correctly — including a real, genuine faithfulness catch during testing (the automated intro-faithfulness judge correctly flagged a fabricated day-of-week in a real generated intro). Zero console errors across tab switch, run selection, and toggle interaction.
- **Server startup**: confirmed clean import of every new/changed module, no exceptions at startup.

Test artifacts (throwaway users `AutoJudgeSmokeTest`/`ToggleOffSmokeTest`, an extra real judged run on `AutoJudgeSmokeTest`) were left in the local dev DB rather than cleaned up, since they're now useful, real example data for the new tab rather than clutter worth removing.
