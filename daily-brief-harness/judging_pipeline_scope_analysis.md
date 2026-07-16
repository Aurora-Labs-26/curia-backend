# LLM-as-a-Judge Pipeline — Scope Analysis

Pure analysis, no code changed. Grounded in a read-through of `daily-brief-eval-buildplan.md`, `app/services/eval_judges_service.py`, `app/services/eval_gold_service.py`, `db/007_eval_judge_schema.sql`, `db/005_eval_gold_schema.sql`, the five judge prompt files under `app/templates/prompts/`, and `app/main.py`'s `/api/eval/judges/*` and `/api/eval/gold/*` routes. Every claim below is either a direct code citation (`file:line`) or explicitly marked as a prior-session note that wasn't re-verified live this pass.

---

## Bottom line

**If you label and run the judges for 30–40 users today, you will not get a single actionable metric out of it — you'll get 30–40 separate case files.** Not because labeling is wasted effort, but because the pipeline conflates two jobs that need to stay separate:

1. **Judge calibration** — "does the judge agree with a human?" (needs labels, small N is fine, done occasionally)
2. **Fleet monitoring** — "is quality trending up or down across real traffic, and where?" (needs zero per-run labels once a judge is calibrated — that's the entire point of building a judge — but needs aggregation infrastructure)

Today, only (1) exists, and only for 3 of 8 judge types, on samples too small for the kappa numbers to mean much. There is no code path to run a judge against a run that isn't manually frozen as "gold," and no code path that rolls multiple runs' judge output into one number. Labeling 30–40 users would just mean doing (1)'s expensive per-item human labeling 30–40 times over, then reading 30–40 individual reports by hand — which is roughly the same amount of human effort as not having a judge at all.

The fix isn't "label more." It's building the monitoring path that doesn't require labels, and being honest about which of the 8 judges are trustworthy enough to feed it.

---

## Part 1 — What "LLM-as-a-judge" is supposed to do here, and what it's conflated into

An LLM judge earns its keep by replacing a human reviewer at a qualifying task the human can't do at scale — fuzzy qualities (faithfulness, tone, relevance-to-intent) that are too subjective for regex but too slow for a person to check on every output. The standard pattern has three distinct stages, and they have different sample-size needs and different consumers:

| Stage | Question it answers | Needs human labels? | Right sample size | Who acts on it |
|---|---|---|---|---|
| **A. Calibration** | Does the judge agree with a human? | Yes | Small (dozens of items), refreshed periodically | You, deciding whether to trust the judge at all |
| **B. Fleet monitoring** | Is real, unlabeled traffic getting better or worse, and where? | No — this is the whole point of having a validated judge | As large as your traffic (30–40 users, ongoing) | You, deciding what to fix next |
| **C. Regression gating** | Did this specific prompt change help or hurt, on the same fixed input? | No (uses frozen inputs, not live labels) | A handful of fixed profiles, re-run on every prompt change | CI / you, before merging a prompt change |

The buildplan (`daily-brief-eval-buildplan.md`) actually lays this out correctly — M2/M3 is stage A, M5 is stage B, M4 is stage C. What's shipped is **only stage A, and only partially**. `eval_judges_service.py`'s own module docstring says it plainly:

> "this only ever runs from an explicit dashboard action ('Run Judges') against an already-frozen gold run, never from the live Pre-Opt / Generate-Brief pipeline... Wiring these judges into the live pipeline for real daily traffic is M5's job (the online dogfood track), not this module's." (`eval_judges_service.py:6-12`)

M5 (stage B) and M4 (stage C) are both explicitly un-started. So right now, the *only* way to get a judge to look at a run is to freeze it as gold — which conflates "I want to calibrate the judge" with "I want to monitor this run," even though those should be two different, differently-sized activities.

---

## Part 2 — Inventory: what actually exists today

### Judge-by-judge

| judge_name | Checks | Model | Gold-validated (kappa)? | Cross-run aggregation? | How often it actually fires |
|---|---|---|---|---|---|
| `relevance` | per-article hit/miss vs. stated interests | Sonnet | **Yes** — `compute_relevance_kappa`, per-profile | No — pooling was built, then deleted (see below) | Every judged run, every article in the labeling window |
| `order_correctness` | is the #1 pick genuinely the most significant in-pool | Sonnet | **Yes** — `compute_order_correctness_agreement`, per-profile | No | Every judged run, once per pool (non-local + local) |
| `order_ranking` | are ranks 2–N in defensible relative order | Sonnet (same call as above) | **Yes** — `compute_order_ranking_agreement`, per-profile | No | Every judged run, once per pool |
| `faithfulness_article` | fabrication vs. source article, critical/moderate/stylistic | Sonnet | **No gold labels exist** | No | Every judged run, once per lead/standard/local segment |
| `faithfulness_bookend` | fabrication vs. structured inputs (name/location/weather/time/picks) | Sonnet | **No gold labels exist** | No | Every judged run, once per intro + outro |
| `tone_flow_pairwise` | generated vs. hand-authored golden script, tone/flow, position-bias controlled (2 calls) | Sonnet | **No gold labels, and no kappa mechanism even if there were** | No | **Only** when a matching golden script exists for that exact `(segment_type, article_url)` — per prior-session notes, ~13 golden scripts exist across all 6 gold profiles combined, so this fires rarely |
| `brief_coherence` | 1–5 Likert whole-brief flow score | Sonnet | **No gold labels** | No | Every judged run, once |
| `overview_fidelity` | intro preview vs. real ranked lineup | Sonnet | **No gold labels** | No | Runs every judged run, but **hidden from the UI entirely** — found to duplicate `faithfulness_bookend(intro)` (per `progress.md`'s Judges-tab redesign notes) |
| `diversity_top4_similarity` | embedding cosine similarity, top-4 | none (local `sentence-transformers`) | Threshold hand-tuned against a few known pairs, not kappa-validated | No | Every judged run |

**3 of 9 checks have any computed agreement number. 5 of 8 LLM calls have zero ground-truth validation of any kind** — their output is "surfaced for spot-check" (`eval_judges_service.py:24-26`), meaning a human still reads every row.

### M0–M6 status (buildplan's own milestones)

- **M0** (instrumentation) — done.
- **M1** (deterministic checks) — done. Solid, but see Part 4C: even this has no cross-run rollup.
- **M2** (gold-labeled test set) — done, but scoped to 6 fixed profiles.
- **M3** (LLM judges, validated) — done for 3 of 8 checks; the rest ship unvalidated.
- **M4** (promptfoo CI regression gate) — **not started**.
- **M5** (online dogfood track — the thing that would let judges run on real, unlabeled, at-scale traffic) — **not started**.
- **M6** (ongoing re-calibration) — **not started**; nothing today reminds anyone to re-check kappa after a judge-prompt change.

---

## Part 3 — Walking through "label 30–40 users, do we get an actionable metric?"

Concretely, here's what happens if you did this today:

1. **You freeze 30–40 runs** via the "Freeze a Run" modal, hand-label each one's non-local pool (up to 8 candidates) and local pool (up to 4), and flag top-pick/order-ranking correctness. That's up to ~480 individual relevance calls plus 60–80 order flags — real, non-trivial human effort.
2. **You click "Run Judges" 30–40 times.** Each run writes fresh rows to `eval_judge_outputs`.
3. **You open `/api/eval/judges/validation-report`.** It computes kappa via `compute_relevance_kappa` / `compute_order_correctness_agreement` / `compute_order_ranking_agreement` — all three group `by_profile` (`eval_judges_service.py:499-509`, `:535-544`) and return `{"per_profile": {...}}` only. **You get 30–40 separate kappa numbers**, each computed from that one profile's own small candidate count (in the one profile that's actually been deeply tested so far, relevance kappa was computed on n=7; order-correctness on n=1 — see `progress.md`'s M3 section). Cohen's kappa on n≈7–12 has enormous confidence intervals; 30–40 of these numbers don't average into anything meaningful because pooling was **built and then deliberately deleted**:
   > "The Judges tab showed 'Pooled across every profile judged so far...' — mixed profiles at completely different iteration stages into one number nobody could act on, and had no consumer besides that one display line." (`progress.md`, Judging #8)
   That was the right call for what existed then (profiles frozen at wildly different points in prompt iteration), but it means **there is currently no code path that produces one trend number from many runs, at all** — not a bad one, none.
4. **For faithfulness, tone/flow, and coherence** (the judges most people would actually care about — "is it making things up," "does it sound natural") — there's no kappa to compute regardless of how much you label, because M2 never built gold labels for those dimensions. You'd be reading 30–40 runs × ~6 segments × 2 faithfulness checks ≈ 360–480 free-text claim lists by eye. That is not meaningfully faster than listening to the briefs yourself — you've built a judge and then used it exactly like not having one.
5. **Nothing in the schema or UI groups by the dimensions that actually map to a fix** — topic/beat, local vs. non-local, segment position, day of week. Even if you eyeballed 400 faithfulness rows and noticed "a lot of these are Local segments," there's no query or view that confirms that impression at a glance; you'd be doing the aggregation in your head from a spot-check table built for reviewing one run at a time.

**So: the 30–40-user experiment as currently instrumentable produces reading material, not a metric.** To get an actual "here's what to fix next" signal at that scale, the aggregation layer has to exist first — and for 5 of the 8 checks, a validated judge has to exist first too, or you're aggregating noise.

---

## Part 4 — Concrete gaps, grouped

### A. No path to run judges without freezing + labeling
`run_judges_for_gold_run` (`eval_judges_service.py:330`) and the one route that calls it, `POST /api/eval/judges/run/{run_id}` (`main.py:537-544`), are named and documented as gold-only. Interestingly, **this is not actually enforced in the data layer** — `get_run_for_judges` (`eval_logging_service.py:322`) queries `harness.eval_runs` directly by `run_id` and never checks `eval_gold_runs` at all. The gold-only restriction is a UI/convention thing, not a schema wall: the only place in the entire app that calls this endpoint is the Gold Set tab's Judges sub-tab, which only ever has a frozen run's `run_id` available (`static/app.js`, `state.goldSet.selectedRunId`). This is good news — it means a "run judges on any recent run, no freeze required" path is a smaller lift than it looks (no migration needed), but **someone has to build the trigger and, more importantly, a view that doesn't require `eval_gold_runs`/`eval_gold_labels` joins to render anything** (today's `compute_*` functions and `list_judge_outputs`'s only caller all assume gold).

### B. Validation coverage is thin, and what exists rests on tiny samples
Only relevance/order-correctness/order-ranking have kappa. Per `progress.md`'s M3 section, only **one** of the six gold profiles ("Niche Topic") has actually been run through judges with real numbers so far — the other five haven't. So today's "validated" judges are validated on n=1 profile's worth of data. Separately: `non_local_top_correct` / `local_top_correct` / `*_order_correct` default `NULL → agree` (`eval_gold_service.py:282-296`, "unmarked = agree, same convention as significance_rank"). That's a reasonable UX default (don't force every candidate to be explicitly clicked), but it means **kappa computed against a mostly-unlabeled gold column is being computed against mostly-default data**, which will silently look more "in agreement" than it is until someone actually goes through and flags the disagreements.

### C. Zero cross-run aggregation anywhere — including for the easy, already-solid M1 checks
This is worth separating from the LLM-judge gaps because it's not a validation problem, it's a missing view. M1's deterministic checks (word budget, TTS-readiness, personalization, structure) already write clean pass/fail/flag rows to `eval_results` for *every* run, gold or not, unlabeled or not (`eval_checks_service.py`). There's no gold-only gate on these at all. And yet there is no "83% of the last 30 runs passed word-budget" view anywhere in the UI — every result is visible per-run only. This is the single cheapest win available: the data to answer "is quality trending, and where" for the deterministic checks already exists in the DB today, unlike the LLM judges, which need validation work first. A basic `GROUP BY check_name, result_status` rollup over `eval_results`, filterable by date range/topic, would be real, immediate, zero-new-cost signal.

### D. Structural blind spots no amount of labeling fixes

- **No recall/completeness check exists anywhere.** Every judge and every human label operates only on candidates that already survived `scoring_service`'s deterministic pre-filter *and* Step 2/3's event-dedup/gate *and* made it into `eval_llm_ranking_output`. A genuinely important story that got wrongly filtered before that point is invisible to the relevance judge, the order-correctness judge, and the human labeler alike — none of them ever see it to say "this should have been here." Labeling window candidates more thoroughly (`DEFAULT_NON_LOCAL_CANDIDATES=8` / `DEFAULT_LOCAL_CANDIDATES=4`, `eval_gold_service.py:24-25`) improves precision measurement, never recall. If missed stories are a real product risk, this needs a different mechanism entirely (e.g., periodically diffing the raw fetch pool against what a broader, independent pull would have surfaced) — not more judge calls on the existing pool.
- **No day-over-day / per-user-over-time comparison.** `eval_runs` are one-off snapshots; nothing compares today's brief for a user against yesterday's. A user getting near-duplicate top stories two days running (a real, listener-facing failure) is undetectable by anything in this system.
- **No judge token-cost logging.** `eval_judge_outputs` has no `input_tokens`/`output_tokens` columns (flagged already in `progress.md`'s open items). Running the full 8-judge suite × 30–40 users, several of which are Sonnet calls with heavy non-optional extended thinking (per `progress.md`'s Score & Curate cost finding, 85–90% of budget goes to thinking tokens on that step), means scaling this experiment is a real, currently-unmeasured dollar cost. You'd be flying blind on cost while also not getting a usable metric out the other end.

### E. Two design inconsistencies worth a second look

- **`brief_coherence` uses a 1–5 Likert score** (`tone_flow_judge.py:38-41`) — but the buildplan explicitly critiqued Likert scoring as a design mistake when reviewing the source PDF: "Categorical / binary / pairwise output instead of 1–10 Likert scores. This is correct: it avoids central-tendency bias (everything scores 6–8)... Models are also empirically better at relative comparison than absolute scoring." (`daily-brief-eval-buildplan.md:65`). `brief_coherence` is the one judge that ended up built exactly the way the plan says not to, and because it has no gold validation, **there's no way to check whether it's exhibiting the exact central-tendency clustering the plan warned about** (everything scoring 3–4, uninformative). A pairwise "does this stitched brief flow better than a shuffled/randomly-reordered control" design would fit the plan's own stated principle better.
- **`tone_flow_pairwise` structurally can't scale.** It requires a hand-authored golden script matching the *exact* article for that exact segment (`get_active_scripts` keys on `(segment_type, article_url)`, `eval_gold_service.py:324-340`). Since real news is different every day, this only ever fires for the small, static handful of golden scripts written during M2 setup — it will essentially never fire for a genuinely new run on fresh daily news, no matter how many users you label. If tone/flow at scale matters, it needs a reference-free design (e.g., an absolute-but-anchored rubric, or pairwise against a stable synthetic baseline rather than a hand-written one per article).

### F. A self-preference-bias regression that happened incidentally
The buildplan is explicit that generator and judge should ideally use different model tiers to limit self-preference bias, and the codebase does this correctly almost everywhere (`ACTIVE_MODEL = "claude-haiku-4-5..."` generates, `JUDGE_MODEL = "claude-sonnet-5"` judges). But `STRONG_MODEL_STEP_IDS = {3, 8, 9, 10, 11, 12, 13, 14}` (`llm_service.py:43`) — **step 3 is Score & Curate**, moved to Sonnet in a recent, well-justified fix for ranking quality (`progress.md`'s Generation #10). Step 3 is also the exact decision `order_correctness`/`order_ranking` (step 9, also Sonnet) exists to independently check. **The judge whose entire job is "did the ranking step get this right" is now the same model family — arguably closer to the same model — as the ranking step it's judging.** This doesn't invalidate the order-correctness numbers, but it's a real, non-obvious increase in exactly the bias the buildplan flagged as a structural limitation to disclose. Worth explicitly re-stating this limitation given the change, and possibly worth being the one judge that gets a genuinely different model (even a free local Ollama model, which the buildplan already floated as an optional mitigation) if this judge is going to be load-bearing for an actionable metric later.

---

## Part 5 — Open questions worth deciding (not prescriptions)

These are genuinely yours to call, not things I'd unilaterally pick:

1. ~~Is the current "label every profile you want judged" workflow the intended end state, or a bootstrapping phase?~~ **Resolved.** Confirmed direction: calibration stays a separate, manual, occasional process; monitoring is fully automatic and unconditional, wired into the live `generate-brief` path itself, no label required per run. See Part 7.
2. **What kappa bar counts as "trust this judge"?** Nothing in the buildplan or the code states a numeric threshold (e.g., Landis & Koch's ≥0.6 = "substantial agreement"). Without one, "is this actionable" has no crisp answer — you're eyeballing a number that might be 0.3 or might be 0.7 with no stated bar for what's good enough to build on.
3. **Do the 5 unvalidated judges (faithfulness ×2, tone/flow, coherence, overview-fidelity) get their own small gold sets, or do they stay "spot-check only" permanently?** Faithfulness in particular is the one the buildplan calls the single most important check ("a fabricated fact in a news product is the worst possible failure class") — it's also the one furthest from having any ground-truth validation today. Worth asking whether that's the right priority order.
4. **Should the labeling/judging window widen beyond the current 8 non-local / 4 local** for judges specifically (not humans) — since a judge doesn't have labeling fatigue, and a wider window would at least extend precision-checking further into the pool, even though it still wouldn't fix the recall blind spot in Part 4D.
5. **What's the right monitoring cadence and cost budget?** Once a scale-monitoring path exists, is it every generated brief, a daily sample, or on-demand — and at what per-user judge cost (currently unmeasured, see Part 4D)?
6. **Should re-validation after a judge-prompt change be enforced somehow**, or does it stay tribal knowledge? M6 says this should be periodic/ongoing; nothing today reminds anyone it's due.
7. **Is per-item output (relevance, faithfulness) or per-run output (coherence, order-correctness) the right grain for each judge**, given aggregation needs to eventually group by topic/beat/segment-position/local-vs-non-local to be actionable? The per-item judges already support this; the per-run ones would need restructuring to attribute a flaw to a specific segment/topic.
8. **Does a judge finding need to map to a specific prompt/file before it counts as "actionable"?** Right now that mapping is entirely manual (a human reads a flagged claim and infers "oh, that's a Local-fetch problem"). Categorization (topic, segment type, local/non-local) would get most of the way there without requiring the judge itself to know anything about the codebase.

---

## Part 6 — What's already solid (don't lose this in the critique)

Worth stating plainly since the rest of this document is mostly gaps: the judge *prompts themselves* are well-built. Binary/categorical/pairwise output instead of Likert (except `brief_coherence`), explicit id-tagging discipline to prevent hallucinated references, a genuinely well-reasoned three-tier severity taxonomy for faithfulness that correctly excludes stylistic connective tissue from being flagged, real position-bias control on the one pairwise judge (`tone_flow_pairwise` runs both orderings and reconciles to "inconclusive" on a flip), and a relevance rubric that's kept deliberately in sync with the generator's own gate criteria rather than measuring a different bar than production actually uses. The gap here isn't judge quality — it's that the harness around the judges never grew past "validate a handful of frozen profiles" into "monitor real scale," which is a scope/infrastructure problem, not a prompt-engineering one.

---

## Part 7 — Confirmed direction: automated per-brief monitoring

Decided (not a proposal): **calibration stays manual and occasional** (freeze a run, label it, compute kappa, in spare time) — **monitoring is fully automatic and unconditional**, wired into the live `generate-brief` path itself. Every brief produces judge output as a side effect of being generated. No button, no freeze, no label.

Concretely, from the three follow-up decisions:

| Decision | Answer |
|---|---|
| Which judges run automatically | `relevance`, `order_correctness`, `order_ranking` (the 3 validated ones) **+ `faithfulness_article`, `faithfulness_bookend`** (unvalidated, but included deliberately — fabrication is the buildplan's single worst failure class, worth watching even before it has a kappa number, as long as its output is read as "flagged for review," not a trusted metric yet) |
| Blocking? | No — background/async. The brief ships exactly as fast as today; judge calls fire after, results land whenever they finish. |
| Sampling | None. Every single "Generate Brief" click in the Multi-User Cache tab triggers judging, no exceptions, no percentage cap. |

### A nuance worth deciding before building: full-path vs. bookends-shortcut runs

`generate_brief_for_user` (`user_brief_runner.py:170`) takes one of two paths depending on whether this user already has a brief for today (see the Reset Brief discussion earlier this session):

- **Full path** (first click for a user, today) — fresh fetch/rank/curate, 5 articles resolved, fresh intro/outro. All 5 automatic judges are meaningful here.
- **`_regenerate_bookends_for_brief` shortcut** (any subsequent click, same user, same day) — reuses the same 5 articles and their cached segment text untouched; only intro/outro are freshly generated. `relevance`/`order_correctness`/`order_ranking`/`faithfulness_article` would just be re-scoring an unchanged article pool and unchanged segment text — real Sonnet spend for a verdict that can't have changed since the last full run. **Recommendation: on a shortcut run, only `faithfulness_bookend` fires** (the one thing that's actually fresh); the other four only fire on a full `generate_brief` run. Flagging this as a recommendation, not a silent assumption — happy to run all 5 on every run regardless if you'd rather not special-case it.

One more caching wrinkle specific to `faithfulness_article`: `article_segment_cache` is shared across every user (that's the whole point of Pre-Opt's cache). Without memoization, the exact same segment text gets a fresh faithfulness_article LLM call every time a *different* user happens to receive that same cached article — real repeated spend checking identical text. **Worth memoizing faithfulness_article verdicts by `(article_id, segment_type)`**, the same key `article_segment_cache` itself uses, so a cached segment gets judged once on first appearance, not re-judged from scratch for every subsequent user who gets served it.

### What this needs that doesn't exist yet

1. **A background trigger** after `generate_brief_for_user` returns (and after the bookends-shortcut path) — fire-and-forget, not awaited inline, so the HTTP response isn't delayed. Structurally this is a smaller lift than it looks: `run_judges_for_gold_run`'s own data fetch (`get_run_for_judges`, `eval_logging_service.py:322`) never actually checks gold status today — the gold-only framing is a UI/naming convention, not a schema wall (see Part 4A). The real new work is the trigger itself, not un-gating storage.
2. **Somewhere to actually see the results.** This is the load-bearing missing piece, not an afterthought: today the *only* UI surface for `eval_judge_outputs` is the Gold Set tab's Judges sub-tab, which requires a frozen run to be selected first. Automated judging on ordinary (non-gold) `generate_brief` runs will happily write rows to the DB — and without a new view, every one of those rows is invisible in the app, permanently. Building the trigger without also building this view produces zero practical value, just a growing table nobody reads. At minimum this needs: a way to browse recent auto-judged runs, and a rollup (pass rate / flagged rate / severity breakdown for faithfulness) grouped by the dimensions that map to a fix — topic/beat, local vs. non-local, day — not just a per-run spot-check table.
3. **Judge token-cost logging** (Part 4D) — this now matters more than it did in the calibration-only framing, because this runs unconditionally, forever, not just during an occasional labeling session. Worth having before shipping the trigger, not after, so the first week of automatic judging doesn't produce a cost surprise.

### A natural volume throttle already exists, worth knowing about

Because of `daily_briefs`' `(user_id, date)` uniqueness, a *full* `generate_brief` run — the one that fires all 5 judges — only happens once per user per calendar day unless Reset Brief is used first (see earlier this session). Realistic volume today is bounded by however often you personally click "Generate Brief" for however many users during dev/testing, not runaway traffic — this pipeline has no scheduled/production delivery wired up yet (per [[daily_brief_system_b_architecture]], System B is dev/harness-only right now). Worth re-checking this assumption if/when real scheduled delivery ships, since the volume math changes completely at that point.

### Revised build order

1. **Judge token-cost logging** — cheap, unblocks knowing what #2 actually costs before it's running unconditionally forever.
2. **The background trigger**, with the full-path/shortcut-path judge-set distinction above, and `faithfulness_article` memoized by `(article_id, segment_type)`.
3. **A non-gold results view** — without this, step 2 ships invisible. At minimum a browsable list + a rollup grouped by topic/local-vs-non-local/day.
4. **Once real volume accumulates**, revisit the per-profile-only kappa limitation (Part 4B) for the 3 validated judges — a rolling window of real automatic verdicts (even without gold labels for most of them) is a very different, much larger sample than the current 6–8 hand-labeled profiles, and may be worth periodically spot-checking against fresh human labels to confirm the judges still hold up at this scale and cadence.
5. **`brief_coherence`'s Likert design and the order-correctness self-preference-bias overlap** (Part 4E/4F) — both cheaper to reconsider now than after a monitoring view is built and people start trusting the numbers on screen.
