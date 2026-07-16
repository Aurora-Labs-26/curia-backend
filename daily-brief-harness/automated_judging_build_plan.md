# Automated Judging — Build Plan

Background/rationale lives in `judging_pipeline_scope_analysis.md` (Part 7 specifically) — this doc is just the build sequence, kept short on purpose. Not started yet; this is what to build, in what order.

**What's being built**: after every "Generate Brief" click in the Multi-User Cache tab, `relevance` / `order_correctness` / `order_ranking` / `faithfulness_article` / `faithfulness_bookend` fire automatically in the background — no freeze, no label, no button. The existing manual Gold Set "Run Judges" button (all 8 judges, calibration workflow) is untouched by everything below — new code runs alongside it, doesn't replace it.

## Hard constraints (confirmed 2026-07-13, apply throughout — also saved as standing project memory, not just for this build)

1. **Milestone 6's UI must be legible to a teammate with zero context**, not just the builder. No unnecessary repeated terminology (don't print "Non-Local"/"Local" on every row when a header already establishes it once — model directly on the Hit/Miss tab's existing redesign: separate tables per pool, header carries the label, rows just show "#3 — title"). Bullet points for itemized output, not paragraphs — model directly on the Gold Set tab's faithfulness display (one bullet per flagged claim, not a run-on sentence). Highlight what matters (severity, flagged rows) instead of uniform visual weight.
2. **The entire automated-judging addition is one bolt-on block with a global on/off toggle — not woven into the existing pipeline, no overlap anywhere.** When off, nothing executes — not "runs and suppresses output," genuinely zero. Concretely: `user_brief_runner.py`'s existing functions (`generate_brief_for_user`, `_regenerate_bookends_for_brief`) each get exactly one new line, at the very end of their success path, calling into a fully separate new module — no new branching, no new state, no changed return shape in the existing functions themselves. All judging logic (Milestones 1-4) lives in that separate module regardless of toggle state; the toggle only gates whether the single call site fires. Plan: a tiny single-row settings table (matches this app's existing all-state-in-Postgres convention better than an in-memory flag that resets on server restart) — flip it via a switch surfaced in the new Judge Output tab itself, keeping the whole feature self-contained in one place.

---

## Milestone 1 — Judge cost logging (do this first)

Everything after this milestone runs unconditionally, forever, once shipped — want real numbers before that starts, not after.

- New migration (`db/013_...sql`): add `input_tokens int`, `output_tokens int`, `latency_ms int` to `harness.eval_judge_outputs`.
- `eval_logging_service.log_judge_output` (`eval_logging_service.py:431`): add the same three as optional kwargs.
- Every `judge_*` function in `eval_judges_service.py` already gets `input_tokens`/`output_tokens`/`latency_ms` back from `llm_service.call_llm` (confirmed at `llm_service.py:249-252` and `:349-352`, both real and simulated paths) — they're just discarded today (`_parse_severity_result` only pulls `parsed`/`worst_severity`/`claims`). This is threading existing data through, not instrumenting anything new.
- `judge_tone_flow_pairwise` makes 2 calls per invocation — sum both.

## Milestone 2 — Make a smaller judge-set callable without touching the existing orchestrator

`run_judges_for_gold_run` (`eval_judges_service.py:330`) runs all 8 judges in one function body. Don't parametrize it — refactor its internals into small per-concern helpers it still calls in the same order (zero behavior change for the manual button):

- `_judge_relevance_and_order(run, pool, ...)`
- `_judge_faithfulness_segments(run, ...)`
- `_judge_bookends(run, ...)` (faithfulness_bookend + overview_fidelity)
- `_judge_diversity(run, ...)` (deterministic)
- `_judge_coherence(run, ...)`

Then add a new `run_automated_judges(run_id, judge_names)` alongside the existing function, calling only the requested helpers. `get_run_for_judges` (`eval_logging_service.py:322`) already works on any `run_id` regardless of gold status — no change needed there. This new function is itself part of the separate bolt-on module from constraint #2 above — it's where all the new logic lives, independent of whether the toggle is on.

## Milestone 3 — Judge-set gating by run kind

| `eval_runs.kind` | Judges that fire | Why |
|---|---|---|
| `generate_brief` (full path) | relevance, order_correctness, order_ranking, faithfulness_article, faithfulness_bookend | Fresh pool + fresh segments + fresh bookends — all 5 are meaningful |
| `regenerate_bookends` (same-day shortcut, `user_brief_runner.py:70`) | faithfulness_bookend only | Reuses the same 5 articles and cached segment text untouched (`user_brief_runner.py:82-83`) — the other 4 would just be re-scoring content that can't have changed since the prior full run |
| `preopt` | none | No user-facing brief; out of scope per the confirmed decision |

## Milestone 4 — Memoize `faithfulness_article` by `(article_id, segment_type)`

`article_segment_cache` is shared across every user (`db/001_schema.sql:141`) — without this, the same cached segment gets a fresh Sonnet faithfulness call every time a *different* user happens to receive that same article.

- Same migration as Milestone 1, or its own: add `faithfulness_severity text NULL`, `faithfulness_detail jsonb NULL` to `harness.article_segment_cache`.
- Before calling `judge_faithfulness_article` for a given article+segment in the automated path, check `get_cached_segment`'s row (`cache_service.py:327`) for an existing value in these columns; only call the LLM if absent.
- `put_cached_segment` (`cache_service.py:346`) does `ON CONFLICT (article_id, segment_type, is_local) DO UPDATE` — whenever a segment's `transcript_json` genuinely changes (regenerated after a prompt edit + cache clear), that same `UPDATE` must also null out `faithfulness_severity`/`faithfulness_detail`, or a stale verdict survives a text change silently.

## Milestone 5 — The trigger itself

Fire from `user_brief_runner.py`, at the end of the success path of both `generate_brief_for_user` and `_regenerate_bookends_for_brief` — `asyncio.create_task(...)`, not awaited (already `import asyncio`'d there, used for segment `asyncio.gather`). Per constraint #2 above, this is a single new line per function, wrapped in the toggle check — everything it calls into lives in the separate module from Milestone 2.

Two things to get right, not skip:

- **New catch-all required.** `log_judge_output`'s own docstring says it deliberately does *not* catch exceptions, because it's "only ever triggered by an explicit dashboard action... never from the live pipeline" (`eval_logging_service.py:449-455`). That assumption is exactly what this milestone breaks. Wrap the new `run_automated_judges` call in its own try/except-and-log at the trigger site, matching the convention `eval_logging_service`/`eval_checks_service` already use for everything else in the live path — leave `log_judge_output` itself unchanged, since the manual gold path still correctly wants errors to surface.
- **Keep a reference to the task.** A bare `asyncio.create_task(...)` with nothing holding the returned Task object can get garbage-collected mid-flight — a known asyncio footgun. Keep a module-level `set()` of in-flight tasks, add on create, discard via a done-callback.

## Milestone 6 — A new top-level tab: "Judge Output"

Without this, Milestone 5 ships into a void — today the only UI for `eval_judge_outputs` is the Gold Set tab's Judges sub-tab, which requires a frozen run selected first.

**Confirmed: a new tab, sibling to Multi-User Cache / Open Coding / Gold Set — not folded into either existing tab.**

- *Not* Gold Set's Judges sub-tab: that UI is structurally built around one selected frozen run (`state.goldSet.selectedRunId`, `loadGoldSetDetail(runId)`) — a cross-run rollup doesn't fit that single-run-drill-down shape.
- *Not* Open Coding: its backing table, `harness.transcript_records`, has no `run_id` column at all (deliberately denormalized/decoupled from `eval_runs`, see `db/001_schema.sql:198-224`) — linking it to judge output would need a schema change first. A standalone tab querying `eval_runs`/`eval_judge_outputs` directly needs none, since they already share `run_id`.

Shape:

- **List**: recent runs with `kind IN (generate_brief, regenerate_bookends)` that have judge output — similar in spirit to the Freeze-a-Run modal's table (`static/app.js:1088`, `loadFreezeRunList`), but sourced from auto-judged runs, not gold-gated.
- **Per-run detail**: reuse `list_judge_outputs` (`eval_judges_service.py:287`) as-is — it already queries by bare `run_id` with no gold join, so it works unmodified here. The existing spot-check rendering built for Gold Set's Judges sub-tab is a reasonable template to copy from, not reinvent.
- **Rollup** (the genuinely new piece, and the actual point of this tab per Part 4C of the scope doc): counts by `judge_name` / verdict / severity, filterable by topic / local-vs-non-local / date range. New endpoint, e.g. `GET /api/eval/judges/recent`.
- **On/off switch**: lives here too, per constraint #2 — this tab is the one self-contained home for the whole feature, control included.

Exact visual layout still worth deciding when actually building it — the tab placement and data-source split above is the part worth locking in now. Rendering must satisfy constraint #1: bullet points over paragraphs, pool label once per section not once per row, highlight flagged/severe items rather than uniform styling — match the Hit/Miss and faithfulness tables' actual output, not just their data shape.

---

## Suggested next session

1. **Milestones 1 + 2 together** — pure additive, zero change to live pipeline behavior, nothing turns on yet. Safe to build and leave uncommitted-to-the-trigger for a bit if you want to sanity-check the cost numbers on a few manual gold runs first.
2. **Milestones 3 + 4 + 5 together** — this is the actual "turn it on" moment.
3. **Milestone 6** — right after, so 5 isn't running invisibly.

**Execution checkpoints** (not permission gates — all of 1–6 is local-only, additive, non-destructive, so none of this should hit an actual approval wall): 1–4 and the wiring for 5–6 are mechanical enough to implement and self-verify (DB checks, curl, Playwright) without new input. Two moments are worth explicitly calling out in a summary rather than quietly rolling past, though: **after Milestone 5** — that's the point unconditional real judge-call cost starts accruing on every future Generate Brief click, and the live pipeline's behavior genuinely changes from there on — and **Milestone 6's visual layout**, which is a first pass matching existing UI patterns, not a final answer.
