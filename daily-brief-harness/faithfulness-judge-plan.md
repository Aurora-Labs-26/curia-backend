# Faithfulness Judge — Redesign Plan

**Context:** part of the Daily Brief LLM evaluation harness. This document covers one specific piece — the judge that checks whether a generated transcript's factual claims are true — and why the current version of that judge needs to change.

---

## 1. The Problem With the Current Approach

### 1.1 What the current system does

The source article (and its description) is passed to the judge LLM along with the generated transcript segment. The judge is instructed to classify every factual claim in the segment against the source, using three severity levels:

- **critical** — a fabricated fact, entity, number, date, or quote not present in, or directly contradicted by, the source (invented statistics, wrong dates, misattributed quotes, people/orgs that don't appear in the source).
- **moderate** — an unsupported inference or editorializing claim that goes beyond the source without being a fabrication (a reasonable-sounding extrapolation, an unwarranted claim of significance, unwarranted causal language).
- **stylistic** — conversational framing/connective tissue/paraphrase with no independent factual claim (never flagged, never included in the claims list).

Output is structured JSON: a `claims` array (each with `text`, `severity`, `explanation`), plus a `worst_severity` rollup (`critical` if any critical claim exists, else `moderate` if any moderate claim exists, else `none`).

### 1.2 Why this is the wrong direction

The judge treats the **source article as the sole ground truth** — anything not traceable to that one article gets flagged as unsupported. That's the wrong model for this product.

Daily Brief is meant to feel properly researched, not like a mechanical summary of a single article. The transcript-generation LLM is expected to draw on broader knowledge — related coverage, established facts, context beyond the one source it was handed. A source-grounded judge will systematically punish exactly the behavior we want: it will flag a true, well-corroborated detail as "unsupported" purely because it doesn't appear in that one article.

**What we actually need to know is different from what this judge measures:**
- Is the claim genuinely true — verifiable somewhere real online?
- Is it something one random anonymous post said, or something reported by multiple independent, credible sources?
- Has the transcript stayed on-topic (this part is already working — the generation prompt is well-written enough that off-topic drift into unrelated stories isn't a real concern here)?

The taxonomy structure (critical / moderate / stylistic) and the JSON output discipline are both worth keeping. What has to change is **what the judge is grounded against.**

---

## 2. What the New Judge Should Do

### 2.1 High-level purpose

Reframe the question from **"does this match the source article"** to:

> Is every factual claim in this segment true and independently verifiable in the real world — and would we be embarrassed if a listener fact-checked it?

The source article becomes reference context to help the judge understand what a claim refers to — not the ground truth it's checked against. A claim absent from the source but true and corroborated elsewhere should not be flagged. A claim that matches the source but is itself false, or only ever said by one unreliable source, should be flagged.

### 2.2 Grounding: relevant prior art (kept simple, not adopted wholesale)

This distinction — checking a generated claim against the world rather than against a single reference document — is an established idea in LLM evaluation research, not something being invented from scratch here. DeepMind's paper on long-form factuality proposes a method called SAFE (Search-Augmented Factuality Evaluator): an LLM decomposes a long response into individual factual claims, then issues its own search queries per claim and reasons over the results to rate each claim as supported, unsupported, or irrelevant. Evaluated against human raters on roughly 16,000 individual facts, SAFE agreed with crowdsourced humans about 72% of the time, and on a sample of disagreement cases it was judged correct more often than the human 76% of the time — while running over 20x cheaper than human annotation.

That result sets realistic expectations: even the reference approach disagrees with human judgment roughly 3 times in 10. The plan below is a deliberately smaller, single-call version of the same idea — no separate claim-decomposition step, no custom search orchestration, no external fact-checking API. It leans entirely on Claude's own web search tool inside one call.

### 2.3 The single call

One Claude Messages API call, web search tool enabled. From the harness's point of view this is still exactly one call site and one JSON output — Claude may issue several searches internally before answering, but that's invisible to the pipeline and requires no extra orchestration code.

**Fed to the judge:**
- The transcript segment being checked.
- The source article, explicitly labeled as reference context, not ground truth.
- The topic and the article's publish date/time (needed to judge recency — see below).

**Asked to do, in one pass:**
1. Extract the atomic factual claims in the transcript (unchanged from the current system — this part works).
2. For each claim, search the web to check whether it's independently corroborated, contradicted, or simply not findable.
3. Classify severity based on what the search turns up — not based on whether the claim appears in the source article.

### 2.4 Redesigned severity taxonomy

- **critical** — the claim is contradicted by search results, or is a specific fabricated detail (number, date, quote, named person/org) that search finds no trace of anywhere.
- **moderate** — the claim is corroborated only by a single low-credibility source (one unverified post or blog, no pickup by any mainstream/wire outlet), or is an inference/editorializing claim that goes beyond what any real source states.
- **unverifiable** *(new)* — search neither confirms nor contradicts the claim, plausibly because the news is too recent (under 24 hours old) to have been re-reported elsewhere yet. This is a critical addition: without it, the judge will systematically mislabel fresh scoops as fabrications simply because search hasn't caught up, punishing the product for being timely. Treat this as a soft flag for human review, not an automatic failure.
- **stylistic** — unchanged. Conversational framing/connective tissue with no independent factual claim. Never flagged.

Rollup priority for `worst_severity`: critical > moderate > unverifiable > none. Keep critical as the hard gate. Treat moderate and unverifiable as tracked, not blocking — at least until enough data exists to know how often each actually shows up.

### 2.5 Output schema (built for aggregation across test cases)

```json
{
  "claims": [
    {
      "text": "the exact claim from the segment",
      "severity": "critical",
      "verification": "contradicted",
      "explanation": "one sentence: what search actually found, or that nothing corroborates this",
      "source_quality": "none"
    }
  ],
  "worst_severity": "critical",
  "counts": {
    "critical": 1,
    "moderate": 0,
    "unverifiable": 0,
    "total_claims": 4
  }
}
```

- `verification` ∈ `{corroborated, contradicted, unverifiable}`
- `source_quality` ∈ `{wire_or_major_outlet, multiple_independent, single_low_quality, none}`

The `counts` block, not just `worst_severity`, is what actually gets aggregated across many test cases and across the week-long dogfood run: critical rate (the hard quality signal), moderate rate (a rigor/tone signal), and unverifiable rate (a signal about news freshness, not generation quality — worth tracking separately so it doesn't get mistaken for a hallucination problem).

---

## 3. Actionable Steps, In Order

1. **Rewrite the judge prompt.** Replace "compare every claim against the source article" with "verify every claim against the real world, using the source as context only." Explicitly instruct the judge not to flag a claim solely for being absent from the source.
2. **Add the `unverifiable` bucket and a recency instruction.** Pass the article's publish time into the prompt; instruct the judge that a claim which would plausibly only be reported by one outlet within the first 24 hours should be marked `unverifiable`, not `critical`, when search finds no corroboration.
3. **Turn on the web search tool for this one API call.** This is the only mechanical/infrastructure change — everything else is prompt-level.
4. **Rebuild the gold-labeled calibration set.** The existing hand-labeled examples were labeled against the source article; a new small set (10–20 claims is enough to start) needs to be hand-checked against the real world instead, since that's now what's being measured.
5. **Run the new judge against that gold set and compute agreement.** Don't expect perfection — SAFE itself only reaches ~72% agreement with human raters. Being in that range is reasonable; being well below it means the prompt needs another pass before it's trusted in the pipeline.
6. **Track all three counts over the week, not just `worst_severity`.** Critical rate is the hard-fail signal. Moderate rate is a rigor signal. Unverifiable rate is a freshness signal — keep it visible but separate, so a spike in it reads as "our news is very current" rather than "our generation quality dropped."

---

## 4. Honest Limitation

Web-search-grounded verification is a real improvement over pure source-matching, but it is bounded by what's indexed and what the search tool surfaces at query time — it is not a truth oracle. The `unverifiable` bucket exists specifically to admit that boundary rather than paper over it, and should be read as "we don't know yet," not as a quiet pass.

---

## 5. Regenerate-on-flag (2026-07-15): making a flag a fix, not just a report

Sections 1–4 made the judge's *verdict* trustworthy. This section makes the *transcript* respond to that verdict — a flagged article segment now gets rewritten and re-checked, up to twice, before it ever reaches a brief marked "ready," instead of the flag just sitting in a report next to text nobody revisits.

### 5.1 Scope: article segments only, not bookends

This applies to `faithfulness_article` exclusively. `faithfulness_bookend` is deliberately untouched — no retry loop, no toggle, same single judge call as before. The reasoning: bookends are checked against structured inputs you fully control (name, location, weather, story picks), so a recurring flag there means the intro/outro prompt itself has a bug worth fixing by hand, not something to paper over with an automated rewrite. Article segments are checked against the real world via search, so some baseline critical/moderate rate is expected and is worth remediating per occurrence.

### 5.2 Trigger: critical + moderate, not unverifiable

A flag only triggers a rewrite if `severity` is `critical` or `moderate`. `unverifiable` never triggers one — it specifically means "search can't confirm or deny this, plausibly because it's too fresh to be re-reported yet" (section 2.4), not "this is probably wrong." Regenerating on it would risk the rewrite scrubbing out true-but-not-yet-indexed content just to satisfy the judge, making the brief blander or less current instead of more correct.

### 5.3 Timing: synchronous, in the critical path

Automated judging used to be a fire-and-forget background task that fired *after* a brief was already marked `ready` and already returned to the caller (`automated_judging.py`) — by the time a flag existed, the brief had already shipped. That's no longer true for `faithfulness_article`: it now runs synchronously inside `user_brief_runner._resolve_one`, for every article segment (cache hit or miss), before the brief is marked ready. A flagged critical/moderate claim is caught and — for a freshly-generated (cache-miss) segment — rewritten before anyone sees it. The tradeoff, accepted deliberately: brief generation is slower when a segment needs retries (each retry is a full generate-then-judge round trip, and the judge call itself does live web searches), in exchange for the guarantee that what ships has already been checked.

`faithfulness_article` was removed from `eval_judges_service.AUTOMATED_JUDGE_SETS["generate_brief"]` as a result — it's no longer a post-hoc check for that run kind, it's inline. `faithfulness_bookend`, `relevance`, `order_correctness`, and `order_ranking` are untouched and still run the old fire-and-forget way.

### 5.4 The loop itself (`app/services/faithfulness_regen_service.py`)

- **Cache miss** (`generate_verified_segment`): generate → judge → if critical/moderate, regenerate with the prior draft + flagged claims fed back in → judge again → repeat, capped at 2 retries (3 attempts total). Every attempt is logged as its own `eval_judge_outputs` row (see 5.6). If no attempt comes back clean, the **least-severe** attempt wins, ties broken toward the **latest** attempt (the most recent rewrite is the model's best attempt at that severity, not a demonstrated regression) — never blindly keep the last attempt if an earlier one was actually better. The winning attempt's verdict is written to the shared `article_segment_cache` row's memoized faithfulness columns, exactly as the old post-hoc path did, so a later cache hit never re-judges this exact text.
- **Cache hit** (`verify_cached_segment`): no regeneration — this text is shared across users and may already be cached/served elsewhere, so rewriting it here wouldn't even be visible to whoever already has it. Reuses the memoized verdict if one exists, or judges once and memoizes it if this cached row predates the feature. Always logs exactly one row (attempt 1).
- **Regeneration prompt** (`USER_ARTICLE_SEGMENT_REGEN_BLOCK`, spliced into `USER_ARTICLE_SEGMENT_PROMPT` via `ArticleSegmentRequest.prior_text`/`prior_feedback`): framed as "fix this correction," not "write it again" — explicitly told to keep the same voice, pacing, and length target as the previous draft, and to fix only the flagged statements (drop or soften a claim it can't verify rather than inventing a replacement). Deliberately does **not** ask the model to preserve the literal opening sentence: the opening is usually where a segment's central (and most flag-prone) claim lives, so "preserve the opening verbatim" and "fix the fabrication" would directly conflict.

### 5.5 Toggle: governs regeneration, not judging

`settings_service.is_faithfulness_regen_enabled()` (`harness.eval_settings.faithfulness_regen_enabled`, `db/016_faithfulness_regen.sql`) is a separate switch from the existing automated-judging toggle. Judging itself always runs synchronously on this path regardless of this toggle — turning it off just means a flagged segment is judged, logged, and left as-is instead of triggering up to 2 rewrites. Same singleton-settings-row pattern as `automated_judging_enabled`, own dashboard toggle ("Regen On/Off") next to the existing "Automated Judging" one.

### 5.6 `attempt_number` and the UI

Every `eval_judge_outputs` row now carries an `attempt_number` (default 1, `db/016_faithfulness_regen.sql`). Almost every segment only ever has one — a full audit trail was worth keeping (a teammate debugging a weird brief can see "attempt 1 had 2 critical claims, attempt 2 fixed it"), but a schema column that's overwhelmingly always `1` isn't a UI feature on its own. Instead, the Judge Output detail view (`static/app.js`'s `faithfulnessBlock`) only shows `(attempt N)` in a segment's header when more than one row exists for that segment_type — the common case still reads exactly as before ("Lead — Clean"), and a retried segment reads as stacked entries ("Lead (attempt 1) — Critical" with its claims table, then "Lead (attempt 2) — Clean" beneath it).

### 5.7 A real wrinkle this created, and how it's handled

`eval_judges_service.run_automated_judges` used to call `delete_judge_outputs_for_run(run_id)` unconditionally at the start of every automated pass — a blanket wipe before rewriting fresh rows. Once `faithfulness_article` started logging its (possibly multi-attempt) rows *synchronously, before* that background pass ever runs, the blanket wipe would have deleted them the moment the background pass fired, destroying the attempt trail for no reason (the background pass doesn't even recompute `faithfulness_article` anymore — section 5.3). Fixed by scoping the delete: `delete_judge_outputs_for_run` now takes an optional `judge_names` filter, and `run_automated_judges` passes `AUTOMATED_JUDGE_SETS[kind]` so it only ever deletes rows for the judges it's about to recompute. `run_judges_for_gold_run` (the manual calibration path) still passes no filter and gets the original full wipe-and-recompute behavior, since that path never runs `faithfulness_article` synchronously in the first place.

### 5.8 What this doesn't cover

- **Already-served briefs.** If a brief was generated before this feature shipped (or before a given article was re-judged), its saved manifest isn't retroactively rewritten — only briefs generated going forward get the synchronous check. No backfill of historical briefs was built.
- **Cost/latency at scale.** A segment needing both retries is 3 sequential generate+judge round trips, each judge call doing its own live web searches. Acceptable at the current "local scale, a handful of users" stage (see Railway risk calibration); worth re-measuring if usage grows before assuming it stays acceptable.
