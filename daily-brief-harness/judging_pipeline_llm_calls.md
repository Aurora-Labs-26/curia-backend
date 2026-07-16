# Judging Pipeline — Every LLM Call

Scope: `app/services/eval_judges_service.py` (the M3 judge layer), covering
both entry points — the manual "Run Judges" button (Gold Set tab) and the
automated background pass that fires after live Generate Brief /
regenerate_bookends calls. All judge calls use `JUDGE_MODEL =
"claude-sonnet-5"` (a stronger/different tier than the Haiku generator, to
mitigate self-preference bias) via the shared `llm_service.call_llm`,
step IDs 8–13. (A step 14 "overview_fidelity" judge existed at the time this
report was first written; removed 2026-07-14 — it duplicated
`faithfulness_bookend`'s check of the intro and mis-flagged accurate content
by comparing against the wrong reference document. See git history for the
removed prompt/functions if needed.)

## Summary table

| Judge (`judge_name`) | step_id | LLM calls per invocation | max_tokens | Tools | Manual gold path | Automated path |
|---|---|---|---|---|---|---|
| `relevance` | 8 | 1 per pool (non-local, local) → up to 2 | 16000 | — | ✅ always | ✅ `generate_brief` only |
| `order_correctness` + `order_ranking` | 9 | 1 per pool → up to 2 (one call answers both) | 8000 | — | ✅ always | ✅ `generate_brief` only |
| `faithfulness_article` | 10 | 1 per segment → up to 5 (1 lead + up to 3 standard + 1 local) | 12000 | `web_search_20260209` | ✅ always fresh | ✅ `generate_brief`, memoized against `article_segment_cache` |
| `faithfulness_bookend` | 11 | 1 combined call (intro+outro judged together) → 2 logged rows | 6000 | — | ✅ always | ✅ `generate_brief` **and** `regenerate_bookends` |
| `tone_flow_pairwise` | 12 | 2 per script judged (A/B position-swap) — segments + bookends, only where a golden script exists | 4000 | — | ✅ conditional on golden script | ❌ never (needs a hand-authored golden script) |
| `brief_coherence` | 13 | 1 per run | 4000 | — | ✅ always | ❌ never |
| `diversity_top4_similarity` | — | **0 — deterministic**, embedding cosine similarity, no LLM call | — | — | ✅ always | ❌ never |

Temperature is never set for any judge call — `JUDGE_MODEL` rejects it as a
deprecated kwarg (unlike the Haiku generator's calls).

## Per-judge summary

**`relevance` (step 8)** — binary hit/miss classifier over one article pool
(non-local or local), checking whether each article clears the same
relevance gate the generation pipeline's own curation step applies. Input:
id-tagged `{title, description}` list + listener interests/location. Output:
one `{id, label, reason}` per article.

**`order_correctness` + `order_ranking` (step 9, one call, two judge rows)**
— given the same pool plus whichever article the pipeline put in the
Lead/Local slot: (1) is that genuinely the most significant story in the
whole pool, and (2) are ranks 2 through the labeling-window cutoff in
defensible relative order. Two separate `eval_judge_outputs` rows, one LLM
call.

**`faithfulness_article` (step 10)** — redesigned 2026-07-14 (see
`faithfulness-judge-plan.md`): checks every factual claim in a segment
script against the real world via the model's own web search tool, using
the source article as reference context only, not ground truth. Severity:
critical / moderate / unverifiable / stylistic (stylistic never flagged).
Only judge with a tool attached. Runs once per segment in the brief — up
to 5 (1 `lead` + up to 3 `standard` + 1 `local`, per `GLOBAL_SLOT_NAMES` in
`pipeline.py` and the `[:4]` non-local cap in `user_brief_runner.py`), not
capped at 3 (that's the count of distinct segment *types*, not segments).
On the automated path, reuses a memoized verdict from `article_segment_cache`
when one exists for that exact `(article_id, segment_type, is_local)`
instead of calling the LLM again.

**`faithfulness_bookend` (step 11)** — checks the intro and outro scripts
against the structured brief inputs they were generated from (listener
name, location, weather, local time, the day's story picks) — no article,
no web search, unchanged taxonomy (critical/moderate/stylistic). Combined
into **one LLM call** (2026-07-14, was two) that judges both scripts
independently in a single pass — intro/outro always come from the same
`eval_meta_segments` row, generated together from the same inputs, so
there's nothing lost by judging them side by side. The response is still
split into two `eval_judge_outputs` rows (segment_type=intro/outro), so
every downstream reader (dashboard, rollup counts) sees the same shape as
before; only the call count changed. The only judge that runs on both
automated `kind`s, since it's the only thing genuinely fresh on a same-day
`regenerate_bookends` call.

**`tone_flow_pairwise` (step 12)** — pairwise comparison of a generated
script against its hand-authored golden baseline, anonymized as "Script
A"/"Script B". Always called twice per script with the pair swapped
(position-bias control) and reconciled in Python — a flipped winner across
the two calls becomes `"inconclusive"`. Gold-only (a golden script must
exist), so it never fires on the automated path.

**`brief_coherence` (step 13)** — 1–5 score for whether the whole stitched
brief (intro + every segment + outro) reads as one continuous listen versus
disjointed segments written independently of each other.

## Orchestration

- **`run_judges_for_gold_run`** (manual "Run Judges" button) — always runs
  every check above (6 LLM judges + the deterministic diversity check),
  always computed fresh, no memoization. Wipes prior judge output for the
  run first.
- **`run_automated_judges`** (background, `app/services/automated_judging.py`
  owns the on/off toggle + error containment) — runs a subset keyed by
  `kind`: `generate_brief` → relevance, order_correctness, order_ranking,
  faithfulness_article (memoized), faithfulness_bookend; `regenerate_bookends`
  → faithfulness_bookend only. Never runs tone_flow_pairwise or
  brief_coherence.

All judge output lands in `harness.eval_judge_outputs`, surfaced in the Gold
Set "Judges" sub-tab (manual runs) and the "Judge Output" tab
(automated runs).
