# Revamp v1 — Transformation + Clustering Redesign

**Branch:** `v2.9` (off `v2.8`)
**Date:** 2026-06-20
**Authors:** Arihant + Claude (claude-opus-4-6)

---

## The Problem

### Transformations kill the author's voice

The current pipeline extracts 7 "primitives" from each article (summary, metadata, key_insights, human_stakes, core_tensions, counterpoints, examples). These are LLM-generated summaries that **replace** the original text as the primary representation downstream.

By the time the podcast transcript is written, it's working from summaries-of-summaries. The original author's phrasing, argument structure, rhetorical style, and voice are gone. The result sounds like an LLM talking about an LLM's interpretation of an article — not like a podcast exploring what the author actually wrote.

### Clustering is semantically narrow

Clustering is based on embedding `core_tensions + counterpoints` only — two of the seven primitives. This means:
- Two articles about the same topic but with different rhetorical framing won't cluster
- Two articles that would make a fascinating contrast (opposing positions on the same issue) might not cluster because their "tensions" are worded differently
- The clustering signal is downstream of the lossy transformation — garbage in, garbage out

### Length is aspirational, not enforced

The briefing packet includes `target_words` and the prompt says "hard constraint, +/- 10%". But:
- The LLM has no structural mechanism to count words — it's just a wish in the prompt
- The merge step (two-host) is supposed to hit target_words but often doesn't because it's consolidating two independently-generated scripts
- There's no post-generation check that truncates or expands — the judge scores quality but doesn't gate on length
- `momentum_loop` (4 min target = 600 words) often runs to 8-10 minutes because the LLM doesn't respect brevity

---

## Proposed Architecture

### Layer 1: Clean Text (always runs, replaces scrape output)

**Goal:** Produce a faithful, normalized version of the original text that any downstream LLM can consume reliably. No semantic changes. No rewriting. No summarizing.

**What it does:**
1. **Strip boilerplate** — nav bars, footers, ad blocks, cookie banners, related-article links, social share buttons, author bios that are site-wide (not article-specific)
2. **Fix encoding** — mojibake, smart quotes, broken Unicode, HTML entities (`&amp;` → `&`)
3. **Normalize structure** — consistent paragraph breaks, clean up excessive whitespace, preserve meaningful formatting (headers, blockquotes, lists, emphasis)
4. **Remove duplicates** — scrapers sometimes grab the same paragraph twice (once from the article body, once from a pull-quote or sidebar)
5. **Preserve author voice** — original word choices, sentence structures, argument flow all stay intact. The clean text should read like the article, minus the website chrome

**What it does NOT do:**
- Summarize, paraphrase, or compress
- Extract structured fields (that's Layer 2)
- Change the order of content
- Add anything not in the original

**Implementation approach:**
- This could be a lightweight LLM pass (fast model, simple prompt: "clean this scraped text, remove web boilerplate, preserve the author's exact words and structure") or rule-based (trafilatura already does some of this, but we need a quality gate)
- Output: `source.clean_text` column — this is what all downstream consumers read
- The raw `full_text` stays for debugging/auditing

### Layer 2: Enrichments (optional, individually toggleable)

**Goal:** Provide supplementary analytical signals that help the clustering and episode generation layers — without replacing the source text.

**Key change:** The briefing packet for the transcript LLM always includes the **clean full text** of each source. Enrichments are metadata on the side, not the primary content.

**Current primitives — disposition:**

| Primitive | Keep? | Rationale |
|-----------|-------|-----------|
| `summary` | **DROP** | Most voice-destroying. The clean text IS the content — why summarize it? |
| `metadata` | **KEEP** | Useful for routing/filtering (category, tone, entities). Doesn't affect voice. |
| `key_insights` | **EVALUATE** | Potentially useful for clustering signal, but may be redundant if we cluster on clean text directly |
| `human_stakes` | **EVALUATE** | Good signal for "is this interesting to a listener?" — but needs ablation to prove it adds value |
| `core_tensions` | **EVALUATE** | Currently the primary clustering signal. May still be useful as a clustering feature, but shouldn't be the ONLY one |
| `counterpoints` | **EVALUATE** | Same as core_tensions |
| `examples` | **DROP** | Rarely useful, often hallucinated or trivially restated from the article |

**Ablation study design:**
- **Baseline:** Generate episodes using ONLY clean text (zero enrichments). Measure quality via the rubric judge + human listening.
- **Add one at a time:** For each enrichment, generate the same episode set with that enrichment added to the briefing. Compare quality delta.
- **Result:** The minimum set of enrichments that measurably improve output quality. Everything else gets dropped.

**Toggleability:**
- `source_insight` table stays as-is, but `process_source` takes an optional `enrichments: list[str]` parameter
- Default enrichment set is configurable (env var or config)
- Ablation harness can run the same source set through different enrichment combinations

---

### Layer 3: Clustering (redesigned)

**Current approach — what's wrong:**
- Embed `core_tensions + counterpoints` → single vector per source → cosine similarity → greedy maximal cliques
- This clusters on a lossy derivative, not on the actual content
- The threshold (0.61) and max size (5) were hand-tuned without validation
- The clique algorithm is greedy and order-dependent — different iteration orders produce different clusters
- No notion of "would these articles make a GOOD episode together?" — only "are these articles similar?"

**The actual question clustering should answer:**
> Given a user's pile of bookmarks, which group of 1-5 articles, when turned into a single episode, would be so attention-capturing that the listener doesn't want it to end?

This is NOT the same as "which articles are similar." Great episodes often come from:
- **Contrast:** Two articles that disagree or approach the same problem from opposite angles
- **Escalation:** Article A introduces a concept, Article B shows where it breaks down
- **Surprise connection:** Two seemingly unrelated articles that share a hidden thread
- **Deep dive:** One rich article that has enough material for a full episode on its own

**Proposed new approach:**

#### Step 1: Embed on clean text (not primitives)

- Use the clean full text (or a representative chunk strategy) as the embedding input
- This captures the actual content and voice, not an LLM's interpretation of the "tension"
- Store as `source_clean_embedding` — separate from the primitive embedding so we can A/B test

#### Step 2: Candidate pair generation

Instead of a single threshold, generate candidate pairs using multiple signals:
- **Semantic similarity** (cosine on clean text embeddings) — "these articles are about related things"
- **Entity overlap** (from metadata enrichment) — "these articles mention the same people/companies/concepts"
- **Category match** (from metadata) — "both are about tech policy" or "both are about neuroscience"
- **Tension compatibility** (if enrichment kept after ablation) — "these articles are in dialogue with each other"

Each signal produces a score. A weighted combination determines candidate pair strength.

#### Step 3: LLM-scored clustering

This is the key change. Instead of using only embedding distance to decide clusters, use an LLM to evaluate candidate groups:

> "Here are 3 articles. If you were making a 10-minute podcast episode from these, would the result be compelling? Rate 1-5 and explain why."

This is expensive per-group, so we only run it on candidate groups that pass the embedding filter. The LLM can evaluate:
- Is there a narrative thread connecting these?
- Would a listener find this combination surprising or obvious?
- Is there enough material here for the target length, or too much?
- Does the combination create energy (contrast, escalation, surprise) or just repetition?

#### Step 4: Cluster selection

From the LLM-scored candidates, select the best non-overlapping set of clusters. This is a set packing problem — we want to maximize total quality while ensuring each source appears in at most one cluster.

**Fallback:** Sources that don't make it into any high-scoring cluster become standalone episodes (current behavior).

---

### Length Enforcement (structural, not prompt-based)

**The problem:** Telling an LLM "write exactly 600 words" doesn't work. It's a known limitation — LLMs can't count tokens/words during generation.

**Proposed approach — structural enforcement:**

#### For single-host:
1. **Outline allocates word budgets per segment.** The outline LLM already produces segments — now each segment gets an explicit word budget: `segment_words = (target_words - intro_words - outro_words) / segment_count`
2. **Generate per-segment, not per-episode.** Instead of one massive transcript call, generate each segment independently with its word budget. This gives the LLM a much smaller target to hit.
3. **Post-generation word count check.** After all segments are generated:
   - If total is < 90% of target: expand the thinnest segment (re-generate with "expand this, add more detail")
   - If total is > 110% of target: trim the longest segment (re-generate with "tighten this, remove redundancy")
   - If within tolerance: ship it

#### For two-host:
1. Same per-segment generation for Host A
2. Host B reacts per-segment (seeing only that segment's Host A output)
3. Merge is per-segment, not per-episode — smaller merge windows = more controllable length
4. Same post-generation check with expand/trim

#### Hard cap:
- After all retries, if still over 110%: **truncate at the segment level** (drop the last segment, adjust outro)
- If still under 90%: flag for human review rather than shipping a thin episode

---

## Open Questions

1. **Clean text LLM vs rule-based?** An LLM pass is more robust to weird scraping artifacts but adds cost and latency. Could do a hybrid: rule-based cleaning first, LLM only if the rule-based output looks suspicious (heuristic: ratio of boilerplate markers found, text coherence score).

2. **Clustering LLM cost.** If a user has 50 sources, that's ~1225 pairs. Even with embedding pre-filter (say 10% pass), that's ~122 LLM calls for pair scoring. Need to batch aggressively or find a cheaper signal.

3. **Per-segment generation tradeoff.** Generating per-segment gives length control but may hurt narrative flow — each segment is generated without seeing the full preceding context. Need to test whether passing "story so far" summary is sufficient.

4. **Ablation infrastructure.** We need a repeatable evaluation harness before we can do ablation. The rubric judge exists but may not be sensitive enough to detect quality deltas from individual enrichments. May need human evaluation for the ablation study.

5. **Migration path.** Existing sources have primitives but no clean_text. Do we backfill clean_text from full_text (re-run the clean pass), or only apply to new ingestions?

---

## Implementation Order

**Phase 1: Clean Text Layer**
- Add `clean_text` column to `source` table
- Build the cleaning pass (rule-based first, LLM upgrade later)
- Update `process_source` to run cleaning before transformations
- Update `briefing_builder` to use `clean_text` instead of primitives as primary content

**Phase 2: Ablation Harness**
- Build a repeatable test harness: same sources × different enrichment sets → episodes → judge scores
- Run ablation on current enrichments
- Drop enrichments that don't improve quality

**Phase 3: Clustering Redesign**
- Embed on clean text
- Build candidate pair generation with multiple signals
- Add LLM scoring for candidate groups
- Replace greedy clique builder with scored cluster selection

**Phase 4: Length Enforcement**
- Per-segment generation with word budgets
- Post-generation word count check with expand/trim
- Hard caps

---

## What We're NOT Changing (Yet)

- The 4 format definitions (narrative_drift, clarity_engine, momentum_loop, exploration_engine) — these are well-designed
- The two-host A/B/merge pipeline structure — the architecture is sound, the inputs are the problem
- The TTS/audio pipeline — working fine
- The rubric judge — useful as-is, may need calibration for new pipeline
- Speaker profiles — independent concern
