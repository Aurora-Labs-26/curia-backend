# Prompt Optimization — When, How, and the Honest Caveats

This doc captures the plan for systematically improving Curia's prompts using DSPy-based optimizers (MIPRO, GEPA). It was originally written before any of the optimization infrastructure shipped — read the *current status* section below first to see what's actually built.

The thesis still holds: **don't run real optimization until you have anchored data.** What changed is that the *infrastructure* to run optimization safely is now in place — the missing piece is collecting the data to optimize against.

---

## Current status (updated as code lands)

### ✅ Shipped — the optimization harness

The "compile a prompt against examples and an evaluator" infrastructure is fully wired:

- **DSPy refactor** — every LLM call is a `dspy.Signature` (in `core/prompts/`). Zero direct `client.messages.create` in active code. This was the prerequisite for any prompt optimization.
- **LLM-as-judge with personalized rubric** — `optimization/rubrics/judge.py` renders a judge prompt from `(company guidelines + user KB)` and runs it against any output. Returns `Judgment(overall_score, preference_score, floor_violations[], feedback)`.
- **Examples CRUD** — `optimization_examples` table + `optimization/examples/` package. QA can `POST /admin/examples` to add trainset rows or `POST /admin/examples/from-episode` to lift inputs from a real production episode.
- **Guidelines (DB-backed + Python fallback)** — `optimization_guidelines` table. QA edits via `PUT /admin/guidelines/{task}`; changes propagate to the rubric live (cache invalidated on write).
- **GEPA runner** — `optimization/runner/gepa_runner.py`. Loads examples → builds `dspy.Predict` for the task → runs GEPA with the rubric judge as metric → saves artifact JSON to disk → updates the run row with baseline/optimized scores.
- **Run lifecycle** — `optimization_runs` table + `optimization/runs/` store. `POST /admin/optimization/runs` queues a run; worker picks it up; status moves `queued → running → completed | failed`. `POST /admin/optimization/runs/{id}/promote` marks an artifact as active for its (task, scope).
- **Dry-run guardrail** — `CURIA_GEPA_DRY_RUN=true` skips the actual GEPA call so you can test the harness without spending optimizer compute.
- **Forward-compat scopes** — examples and runs both support `scope_type ∈ {global, cohort, user}`. Per-cohort and per-user prompt artifacts are a YAML/runtime change away once you have the data.

### ❌ Not yet shipped — the parts that make optimization meaningful

The infrastructure is ready. The data and the runtime swap aren't:

- **Real eval data** — there are no listened-to-episodes yet, no behavioral signal (completion rates, skips), no user-rated examples. Without anchors, judge-only optimization will hill-climb toward whatever the judge happens to like.
- **Pair-comparison shadow runs (Path 2 in §4)** — runs the existing prompt + a candidate in parallel for some % of jobs and stores `prompt_pair_comparisons` rows with judge verdicts. Not built; ~3 hours.
- **Behavioral metric** — using `episode.quality_score` is a judge proxy, not real anchor. Real anchor needs `episode_listens` (FUTURE_THESIS_1 Phase B). Not built.
- **Cohort clustering** — k-means on KB+behavioral feature vectors, then per-cluster GEPA runs. Not built.
- **Runtime artifact loader** — `studio/generator.py` still uses `dspy.Predict(GenerateTranscript)` directly. The promoted artifact JSON in `prompts/optimized/{task}/{scope_type}/{scope_value or 'global'}/<run_id>.json` isn't picked up at request time yet. **This is the most consequential gap** — until it ships, "promote" is a no-op for end users. ~30 LOC fix.

### What this means today

You can do this *right now* through the QA endpoints:

```
1. POST /admin/examples           ← curate a trainset (or use from-episode for real inputs)
2. POST /admin/optimization/runs  ← kick off GEPA against the rubric metric
3. GET /admin/optimization/runs/{id} ← watch baseline/optimized scores
4. POST /admin/optimization/runs/{id}/promote  ← mark artifact active
```

What you cannot do yet:
- Have a promoted artifact actually shape user-facing transcripts (loader gap above)
- Anchor the judge in real listening behavior (needs FUTURE_THESIS_1 Phase B)
- Optimize per-cohort or per-user cleanly (clustering not built)

So: **the harness is real. It runs end-to-end. But until the loader ships and behavioral data exists, this is dress rehearsal — useful for QA to test the pipeline, not yet for production prompts.**

### Where we are on the phased path (§9)

| Phase | What | Status |
|---|---|---|
| Wait | "Don't optimize without data" | ❌ broken — we built the harness anyway, but the recommendation still holds for *running* it |
| Add shadow infra | Pair-comparison shadow runs | ❌ not started |
| Collect data | Behavioral + pair-comparison data | ❌ not started (needs Phase B from FUTURE_THESIS_1) |
| Distill (optional) | Synthesize gold for cheap-model targets | ❌ not started |
| Real optimization | MIPRO on transformations | ⚠ infrastructure ready; not run with real data yet |
| Real optimization 2 | GEPA on outline + transcript | ⚠ same |
| Per-segment optimization | Per-format, per-show artifacts | ❌ scope supported in DB, no runs done |
| Per-user-cluster optimization | Cluster assignment + per-cluster prompts | ❌ clustering not built |

---

---

## 1. The thesis

Curia has five prompts that drive product quality:

1. The five ingest transformations (`key_insights`, `human_stakes`, `core_tensions`, `counterpoints`, `examples` in `core/ingest.py`)
2. The idea evaluation prompt (`BATCH_EVAL_PROMPT` in `intelligence/idea_generator.py`)
3. The outline generation prompt (`OUTLINE_PROMPT` in `studio/shows/prompts.py`)
4. The transcript generation prompt (`TRANSCRIPT_PROMPT` in same)
5. The (future) companion reflection prompt (`COMPANION_PROMPT`, see Future Thesis 1)

These are exactly the kind of compound LLM pipeline that DSPy + MIPRO/GEPA were built for. **But:** running optimizers without an eval set is theater. The optimizer hill-climbs whatever metric you give it, and a metric driven by an LLM judge with no human anchor will systematically warp prompts toward what the judge happens to like — usually longer, hedgier, more confident-sounding outputs that users like *less* than the originals.

**So the honest order of operations is: build the eval data first, then optimize.** This doc lays out the four paths to do that and which one to take.

---

## 2. Why "no eval set" is a bigger problem than it looks

A naïve approach is: "just have an LLM judge score the outputs." This appears to give you a metric for free. It doesn't, for three reasons:

1. **Judge drift.** Most LLM judges score outputs higher when they're longer, more polished, and more hedged. Optimizing toward such a judge produces transcripts that score 4.5/5 against the rubric and bore real listeners.

2. **No ground truth = no calibration.** Without at least *some* human labels, you cannot tell whether the judge correlates with actual user preference. A judge that confidently agrees with itself is not a useful judge.

3. **Overfit risk is high.** Optimizers will gladly find prompt variants that exploit judge quirks (specific phrasings the judge rates well, output structures the judge prefers). The optimized prompt looks better only against that judge.

The fix is not "find a better judge." The fix is **anchor the metric in real signal** — either explicit human labels or behavioral data from real users.

---

## 3. The five prompt targets, ranked by optimization fit

| # | Prompt | Best optimizer | Eval set type needed |
|---|---|---|---|
| 1 | Ingest transformations | MIPRO (per-transformation, 5 separate runs) | 30–50 hand-rated extractions per type |
| 2 | Idea evaluation | MIPRO for JSON shape, GEPA for angle quality | 30 cluster groups with rated outputs |
| 3 | Outline generation | GEPA | Format-fidelity + structural rubric, 50 examples |
| 4 | Transcript generation | GEPA | Behavioral signal (completion %) is the gold metric |
| 5 | Companion reflection | GEPA | Behavioral (was the journal entry valuable?) |

Transformations are easiest because the task is narrow ("extract X from Y"). Transcript is hardest because "good" is multidimensional and qualitative.

---

## 4. The four paths forward

### Path 1 — Skip optimization, build the data flywheel first (recommended)

Order:
1. Ship BACKEND_PLAN v1 (the API + worker + Postgres baseline)
2. Ship Phase A + B from Future Thesis 1 (events table + listening telemetry)
3. Run the product. Real users save articles, generate episodes, listen. Data accumulates organically.
4. After ~50 episodes have meaningful play data, *that* is your eval set. `completion_pct`, `skip_count`, explicit feedback — these are vastly more useful than anything you'd hand-label.
5. *Then* run MIPRO/GEPA against behavioral metrics.

This is the strongest path because:
- The metric reflects what users actually do, not what someone *thought* was good.
- Optimization improvements are real product wins, not eval-set wins.
- You don't burn a week labeling things.

Downside: nothing happens for ~3 months while you wait for data. Acceptable.

### Path 2 — Pair-comparison shadow runs (the clever path; do this in parallel)

Run during the data-flywheel period. Costs ~$0.10–0.30 per episode in extra compute.

Mechanism: for each ingest job and each episode generation job, **run two prompt variants in parallel** (the production prompt + a candidate). An LLM judge picks the winner with a structured rubric. Both outputs are stored.

Over weeks of normal usage you build a preference dataset:
```json
{
  "input": "...",
  "output_A": "...",  // existing prompt
  "output_B": "...",  // candidate
  "winner": "A",      // judge pick
  "rationale": "...", // judge reasoning
  "metadata": {"prompt": "transcript", "format": "narrative_drift"}
}
```

DSPy supports preference-based optimization directly from this shape. You never had to sit down and build an eval set — it accumulated as a side effect of using the product.

**Mitigation for judge bias:** use *two* different judge prompts (or two different models — Sonnet + GPT/Gemini if licensing allows), and only count examples where both agree. Cuts data volume but eliminates the worst bias.

### Path 3 — Synthetic gold from a stronger model (distillation)

Different goal: not to make the *best* prompt better, but to make a *cheap* model match an *expensive* one.

Use Sonnet to generate "gold" outputs for sample inputs. Use those as MIPRO targets for Haiku. The Haiku prompt gets optimized toward Sonnet's outputs. Result: Haiku-quality cost at near-Sonnet quality.

Useful for: ingest transformations, where you'd love to run on Haiku for cost but quality matters.

Not useful for: outline / transcript, where Sonnet *is* what runs in production.

Run this **after** behavioral data exists, as a cost-reduction pass.

### Path 4 — Tiny hand-rated set + rubric (the "I want a result this month" path)

If you really need to optimize something now:

- Pick 20 already-ingested sources from production
- Spend ~90 minutes rating each transformation output 1–5 against an explicit rubric
- That gives you 100 anchor labels (20 sources × 5 transformation types)
- Run MIPRO on transformations *only* — narrow tasks where 20 examples is enough
- Hold out 5 sources for validation

Limits: enough data for transformations, not enough for outline/transcript. Don't try.

This is fine if you're impatient and want a real (small) win. It's not a substitute for the flywheel.

---

## 5. Implementation: pair-comparison shadow runs

This is the only path with concrete code work to do *now*, since it bolts onto the worker once it ships. Everything else waits.

### 5.1 Architecture

```
┌────────────────────────────────────────┐
│  Worker handler: generate_episode      │
│                                        │
│  if SHADOW_PROMPTS_ENABLED:            │
│    output_A = run(production_prompt)   │
│    output_B = run(candidate_prompt)    │
│    judge    = llm_judge(A, B, rubric)  │
│    save_pair_comparison(A, B, judge)   │
│    use output_A in production          │   ← never use B in production
│  else:                                 │
│    output = run(production_prompt)     │
│    use output                          │
└────────────────────────────────────────┘
```

Key invariants:
- The candidate is never user-facing. The user always gets the production output. Otherwise you're A/B testing on users without consent.
- Shadow runs cost real money. Behind a feature flag, off by default, on for a sample of users / jobs.
- Judge runs on a *different* model from the candidate generator (avoid self-preference bias).

### 5.2 Schema additions

```sql
CREATE TABLE prompt_pair_comparisons (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_kind     TEXT NOT NULL,            -- transformation:key_insights | outline | transcript | ...
    input_hash      TEXT NOT NULL,            -- sha256 of input — dedup
    input_data      JSONB NOT NULL,           -- the actual input fields
    variant_a_id    TEXT NOT NULL,            -- e.g. 'production'
    variant_a_output JSONB NOT NULL,
    variant_b_id    TEXT NOT NULL,            -- e.g. 'candidate_2026_01_v1'
    variant_b_output JSONB NOT NULL,
    judge_a         TEXT NOT NULL,            -- e.g. 'sonnet'
    judge_b         TEXT,                     -- second judge if dual
    winner          TEXT NOT NULL,            -- 'a' | 'b' | 'tie' | 'disagree'
    rationale_a     TEXT,
    rationale_b     TEXT,
    metadata        JSONB DEFAULT '{}'::jsonb,  -- format, user_id, source_count, etc.
    correlation_id  UUID,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX prompt_pair_kind_idx ON prompt_pair_comparisons (prompt_kind, created_at DESC);
```

### 5.3 Code surface

```
core/
└── prompt_lab/                              ← NEW
    ├── __init__.py
    ├── shadow.py                            ← run_shadow(prompt_kind, input, candidate_id)
    ├── judges/
    │   ├── transformation.py
    │   ├── outline.py
    │   └── transcript.py
    ├── candidates/                          ← versioned candidate prompts as JSON files
    │   ├── transcript_2026_01_v1.json
    │   └── outline_2026_01_v1.json
    └── analysis.py                          ← reads pair_comparisons, reports win rates
```

### 5.4 Activation strategy

- `SHADOW_PROMPTS_ENABLED=true` env var
- `SHADOW_SAMPLE_RATE=0.2` (20% of jobs run shadow)
- Per `prompt_kind`, designate one current candidate. Multiple candidates = multiple parallel shadow runs (more cost; not v1).
- Daily Cron / `analysis.py` reports win rates: "transcript_2026_01_v1 wins 58% of pair comparisons (n=147)."

### 5.5 Promotion to production

A candidate gets promoted only when:
- ≥100 pair comparisons collected
- Win rate ≥55% with both judges agreeing
- No user complaints in feedback signal during the shadow window

Promotion is a config change: the candidate JSON becomes the production prompt; a new candidate enters shadow.

---

## 6. The eval set you'll have eventually

Combining behavioral data + pair comparisons, after ~3 months of usage:

| Data source | Volume | Use |
|---|---|---|
| `episode_listens` (Thesis 1) | ~500 episodes × completion data | Transcript optimization metric (behavioral) |
| `episodes/:id/feedback` (Thesis 1) | ~50–200 explicit ratings | Anchor for judge calibration |
| `prompt_pair_comparisons` | ~300+ pairs across prompts | Direct preference data for DSPy preference learning |
| `events` skip patterns | ~thousands | Find systematic failure modes |

That's a much richer eval set than you'd ever build by hand-labeling. Worth the wait.

---

## 7. Costs

### Shadow runs (Path 2, ongoing)
- 20% sample × ~$0.50/episode × 2 (shadow doubles cost) × 1 judge call (~$0.02) ≈ **+$0.12/episode**
- For 100 episodes/day = +$12/day = ~**+$360/month**

### Optimization runs (when triggered)
- MIPRO on transformations (5 prompts × ~70 trials × ~50 examples × Haiku) ≈ **~$90/run**
- GEPA on transcript (50 examples × reflective Sonnet calls) ≈ **~$200–400/run**
- Run frequency: ~quarterly per prompt, plus after model upgrades
- ~**$1k–2k/year of optimization compute**

### Storage
- `prompt_pair_comparisons` is verbose (full inputs + two outputs per row). At 100 episodes/day with 20% sampling, ~600 rows/month. Bytes-level cost; ignore.

---

## 8. Risks

1. **Overfitting.** Always hold out a validation set the optimizer never sees. Recompute the metric on it after each run.
2. **Judge bias.** Mitigate with dual judges; only trust agreements.
3. **Prompt drift toward unreadability.** Optimized prompts can be longer, weirder, harder to maintain. Cap instruction length explicitly. Reject candidates that would be embarrassing to read aloud.
4. **Local optima.** MIPRO and GEPA both get stuck. Multiple runs from different seed prompts. Don't trust a single run.
5. **Cost spikes during sweeps.** Set hard daily budget caps in DSPy config.
6. **Behavior drift.** Optimized prompts that work for user cohort X fail for cohort Y. Per-segment optimization (per format, per show, eventually per user-cluster) addresses this — see §10.
7. **Lock-in.** Once you have an optimized prompt artifact in production, swapping models becomes a re-optimization cycle. Plan for it.

---

## 9. Phased order

Aligning with BACKEND_PLAN and Future Thesis 1:

| Phase | What | When | Prereq |
|---|---|---|---|
| Wait | Don't optimize yet | Now → end of v1 alpha | — |
| Add shadow infra | Wire `core/prompt_lab/` + `prompt_pair_comparisons` table; flag-gated | Right after BACKEND_PLAN v1 ships | v1 worker exists |
| Collect data | Behavioral (Thesis 1 A+B) and pair-comparison data accumulates | 8–12 weeks of normal usage | Shadow infra in place |
| Distill (optional) | Path 3 — synthesize gold for Haiku transformations | After shadow accrues enough variants to know which model tier each prompt should run on | Shadow data exists |
| Real optimization | MIPRO on transformations using behavioral + pair data | After ~50 episodes with completion data | Eval set exists |
| Real optimization 2 | GEPA on outline + transcript | After transformation pass succeeds | Lessons from MIPRO run |
| Per-segment optimization | Per-format, per-show optimized prompts | After single-prompt optimization is stable | Stable infra + clear win |
| Per-user-cluster optimization | k-means on user models, optimize prompt per cluster | When power users are clustered enough to make this worth it | Mature companion data |

---

## 10. The end state — per-user prompt personalization

The reason all this is worth doing: at the end of the road, **different users get prompts literally tuned to what they've finished listening to**.

Mechanism, eventually:
- Cluster users by `companion_state.preferences` into ~5–10 taste profiles (k-means or hand-defined buckets)
- Maintain an optimized transcript prompt per cluster
- Assign new users to the closest cluster based on first ~5 listens
- Re-cluster + re-optimize quarterly

Not a v1 feature, not a v2 feature. But this is the destination — and every doc here points at it. The shadow infra collects the data. Future Thesis 1 collects the behavioral signal. The optimizer turns both into per-cluster prompts. The result is a podcast generator that *learns from your listening* in a way that's structurally impossible for a single-prompt product.

That's the long-term thesis. Everything in this doc is in service of it.

---

## 11. How this connects to the other plans

- [BACKEND_PLAN.md](BACKEND_PLAN.md) ships the substrate: workers can run shadow variants, Postgres holds pair comparisons, the prompt is loadable from a versioned artifact instead of being hardcoded.
- [FUTURE_THESIS_1.md](FUTURE_THESIS_1.md) ships the behavioral signal: listening telemetry becomes the gold metric for transcript optimization.
- This doc waits for both. Then it pulls the trigger on real optimization with real data.

If you're tempted to skip ahead and run MIPRO before this groundwork: don't. The cost is non-zero, the lift is small, and the risk of warping the existing well-crafted prompts is real. The patient path wins.
