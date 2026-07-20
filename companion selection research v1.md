# Companion Selection — Research Synthesis v1

**Date:** 2026-07-16
**Question:** Given a seed doc and a bucketed corpus (N≈100–5000), select ≤4 companion
docs that make a maximally engaging multi-source podcast episode. Similarity alone is
known-wrong; good companions include contrast, escalation, cross-domain connection,
and background.
**Method:** deep-research workflow (5 search angles → 30 sources → claim extraction →
adversarial verification). The first run hit a usage limit before final synthesis; this
doc is a manual synthesis of the salvaged material. Claims marked ✅ survived 3-0
adversarial verification; unmarked claims were extracted with citation but not
triple-verified. A resume run is re-verifying the rest — live status below.

<!-- VERIFY_STATUS_START -->
**Verification — FINAL (2 runs, both limit-capped at the synthesis step):**
- **14 claims fully verified 3-0** (marked ✅ throughout)
- **~72 more claims upheld with 1+ adversarial vote and zero refutes** (their
  remaining votes were lost to usage limits — treat as strong-but-not-triple-verified)
- **0 claims killed.** 3 claims carry a single refute vote each — all three are the
  DPP NP-hardness statements, where the refuter accepted the theorem citations as
  accurate but judged the phrasing an overreach of what the quoted theorem supports.
  The practical takeaway (exact DPP MAP is intractable; greedy approximation is what
  everyone runs) stands.
- The workflow's own merge step never ran; §1–2 below are the manual synthesis and
  remain the canonical read.
<!-- VERIFY_STATUS_END -->

---

## 1. What each family actually optimizes (and why it's not our objective)

### MMR (Carbonell & Goldstein 1998)
- Greedy: relevance minus similarity-to-already-picked. One λ knob on a single
  relevance↔novelty axis. ✅
- Optimizes **anti-redundancy, not complementarity** — no notion of contrast,
  escalation, or background. ✅
- Demonstrated win: ~20% more distinct propositions at equal summary length (λ=0.3)
  in multi-doc news summarization. ✅ Cheap (reranker over a candidate set).
- Formal wart: MMR's criterion is submodular but **not monotone**, so the greedy
  (1−1/e) guarantee doesn't apply to it.

### DPPs / k-DPPs (Kulesza & Taskar)
- Set-wise probabilistic objective: per-item quality × pairwise diversity
  (det of kernel submatrix). ✅
- Fits our shape scarily well *mechanically*: **conditional DPP = "seed fixed, pick
  companions"** (closed form ✅); **k-DPP = "exactly k"**; N≈5000 is interactive-speed
  (O(N³) eigendecomp, 2–3s) ✅; MAP is NP-hard but greedy is constant-factor ✅.
- **Structural disqualifier: DPPs encode only negative correlations** — they can make
  items repel, never attract. Contrast pairs, escalations, background-of-seed are
  *positive* co-occurrence preferences a DPP cannot express. Best used as a
  diversity prior/baseline, not the selector.

### Submodular coverage (Lin & Bilmes 2011)
- Coverage + diversity-reward, monotone submodular → greedy ≥ (1−1/e) of optimal at
  any N. ✅
- The diversity reward is computed **per-cluster with diminishing returns — a direct
  fit for our topic buckets** ("second doc from the same bucket is worth less"). ✅
- DUC-04: coverage+diversity beat every entrant; pure similarity-to-corpus scored
  far worse — literature-grade confirmation that similarity alone is the wrong
  objective. ✅

### Complementary-item recommendation (Sceptre, SHOPPER, FBL)
- The *concept* transfers: complements are **directional/asymmetric** (companion adds
  what the seed lacks — not a symmetric similarity), and the survey explicitly
  proposes document/news transfer including deliberate opposing-viewpoint selection.
- Complements are empirically **harder than substitutes**, and similarity baselines
  fail on them — again: similarity ≠ companionship.
- The supervision obstacle: e-commerce learns from co-purchase logs; documents have
  no such signal. The transferable escape: **Function-Based Labels** — a 9-type
  relationship taxonomy labeled by an LLM judge (GPT-4o-mini: 0.849 accuracy vs
  humans at 1/842 the cost), then **distilled into a small classifier** (ModernBERT,
  2,759 pairs → 0.911 F1). Label-with-LLM → distill-to-free is a proven pattern.
- Sceptre precedent: exploiting a **hierarchical category taxonomy** to structure the
  relation model (our two-tier buckets).

### Narrative extraction (Connecting-the-Dots, Metro Maps, Narrative Trails)
- **Local pairwise similarity chains produce globally incoherent narratives** (the
  Clinton→Microsoft→Palestinian-markets drift). Coherence must be a *global* set/chain
  property. ✅-adjacent (multiply sourced)
- Coherence is best formalized as **weakest-link (max-min transition), not a sum** —
  one bad transition ruins an episode even if the rest are strong. Narrative Trails
  (2025) re-confirms: bottleneck objectives beat additive ones, and run fast at
  exactly our N.
- Metro Maps' selection objective is a *triple*: *coherence + coverage (submodular,
  bucket-diverse) + connectivity* (threads must intersect). Its human evals beat
  Google News baselines decisively (84.5% vs 74.2% score; 72% MTurk preference).
- Maximizing coherence alone picks coherent-but-boring sets — importance/coverage
  must be co-optimized.

### LLM-as-selector / rerankers
- **Setwise prompting** (compare c docs per prompt): −46% LLM calls, −62% tokens vs
  pairwise at equal-or-better quality, and **robust to candidate ordering** (matters:
  bucket candidates have no meaningful prior order).
- **Do NOT dump all candidates in one long prompt**: single-pass long-context
  listwise degrades 22–27% vs sliding-window; JointRank's blockwise + PageRank
  aggregation: nDCG 70.88 vs 57.68 for full-context.
- LLM editorial judges: excellent **triage** (candidate identification F1=0.94) but
  mediocre final "newsworthiness" calibration (r≈0.52 vs humans; overrate
  novel-but-vague, underrate concrete-impact). Use LLMs to type/filter; keep the
  final assembly rule-shaped or set-comparative, not "pick the most interesting."
- Cost-control ladder: distillation (RankZephyr ≈ RankGPT-4 at a fraction of the
  size); dedicated cross-encoders are ~60× cheaper than LLM rerankers and sometimes
  more accurate (vendor-benchmarked; treat as directional).
- A daily retrieve-then-LLM-judge pipeline over a news stream ran at **$0.15/day** —
  our scale is trivially affordable.

### Products (NotebookLM, PodAgent, Public Service Algorithm)
- **NotebookLM does no source selection at all** — the user curates; generation
  auto-surfaces cross-doc connections; quality *degrades* on large undifferentiated
  sources and is best on **small curated sets**. Our ≤4-companion constraint is
  product-validated.
- **PodAgent**: deliberately engineering viewpoint diversity raised GPT-4-judged
  Engagingness by +1.25–1.45 (on −3..3) — direct evidence for the
  contrast-increases-engagement premise.
- PodAgent's 5-dim judge (Coherence, Engagingness, Diversity, Informativeness,
  Speaker-diversity) and PodEval's text/speech/audio split are reusable eval
  templates.

### Evaluation (the uncomfortable truth)
- **No standard diversity metric measures contrast/complementarity** (α-nDCG,
  ILAD/ILMD, coverage metrics — all similarity-derived). ✅-adjacent
- Human ground truth for "engaging/newsworthy" is unstable (κ as low as 0.03–0.29);
  but **LLM judges are internally consistent** (ICC>0.92) and correlate ~0.65 with
  mathematical coherence — good enough for *relative* A/B comparisons, not absolute
  scores.
- Cheapest useful protocol: **pairwise set preference** (two candidate sets, judge
  picks the better episode premise) — the k-DPP paper's MTurk template.

---

## 2. Ranked architectures for Curia

### 🥇 A. Staged funnel: bucket-aware candidates → LLM relation typing → role-coverage assembly
1. **Candidates (free, deterministic):** from buckets + embeddings — same-Tier1 docs
   (depth), mid-similarity docs from *other* Tier1s (cross-domain candidates; use a
   similarity *band*, not top-k — top-k is redundancy), entity-overlap docs.
   Pool ≈ 20–40.
2. **Relation typing (cheap LLM, cacheable):** classify each (seed, candidate) pair
   into a document-FBL taxonomy:
   `background | supports-with-new-evidence | contrasts | escalates/complicates |
   cross-domain-analogy | redundant | unrelated`. Haiku, ~30 pairwise calls ≈ cents;
   **results stored per pair → reusable across future episodes; distill to a small
   classifier later** (0.911-F1 precedent).
3. **Set assembly (rule-shaped, not vibes):** pick ≤4 to satisfy a **role-coverage
   objective** — e.g. ≥1 contrast/escalation, ≤1 background, 0 redundant, bucket
   diminishing-returns (Lin–Bilmes-style) — via greedy scoring; optionally one
   **setwise** LLM call comparing the top 3–5 assembled sets (setwise = the
   robust/cheap prompting mode).
- **Why it wins:** encodes positive complementarity (the thing DPP/MMR structurally
  can't), uses LLMs where they're proven strong (pair relation typing: 0.85–0.99
  agreement) and not where they're weak (absolute interestingness), costs cents,
  produces auditable artifacts (typed pair relations), and the relation store
  compounds in value.

### 🥈 B. Conditional k-DPP / submodular selection over enriched kernels
Principled math, seed-conditioning built-in, interactive at N=5000, zero marginal
LLM cost. But it can only *repel* — no contrast/escalation semantics. **Right role:
baseline for the ablation, and possibly the Stage-1 candidate diversifier inside A.**

### 🥉 C. Pure LLM setwise selection (JointRank-style over candidate blocks)
Simplest to build; but the evidence says final-selection calibration is the LLMs'
weak spot (r≈0.52), long-context degrades, and it leaves no reusable artifacts.
**Right role: ablation arm to prove A earns its structure.**

### Evaluation plan (any architecture)
Pairwise set-preference LLM judging (position-swapped) + PodAgent's 5-dim transcript
judge on generated episodes; small human listening spot-checks. Never absolute
scores — always A-vs-B on the same seed.

---

## 3. Sources (30, grouped by angle)

**Diversified retrieval / set selection**
- [The Use of MMR, Diversity-Based Reranking (Carbonell & Goldstein, 1998)](https://aclanthology.org/X98-1025.pdf)
- [Determinantal Point Processes for Machine Learning (Kulesza & Taskar, 2012)](https://arxiv.org/pdf/1207.6083)
- [k-DPPs: Fixed-Size Determinantal Point Processes (ICML 2011)](https://icml.cc/2011/papers/611_icmlpaper.pdf)
- [A Class of Submodular Functions for Document Summarization (Lin & Bilmes, ACL 2011)](https://www.semanticscholar.org/paper/A-Class-of-Submodular-Functions-for-Document-Lin-Bilmes/f5ce3e9636bed47b377405cd85d3a4abc3b3a234)
- [Max-Sum Diversification, Monotone Submodular Functions and Dynamic Updates (Borodin et al.)](https://arxiv.org/pdf/1203.6397)
- [Result Diversification in Search and Recommendation: A Survey (Wu et al.)](https://arxiv.org/pdf/2212.14464)
- [Uncovering the Bigger Picture: Comprehensive Event Understanding via Diverse News Retrieval (NEWSCOPE)](https://arxiv.org/html/2508.19758v1)

**Complementary-item recommendation**
- [Complementary Recommendation in E-commerce: Definition, Approaches, Future Directions (survey)](https://arxiv.org/pdf/2403.16135)
- [Inferring Networks of Substitutable and Complementary Products (McAuley et al., Sceptre)](https://arxiv.org/pdf/1506.08839)
- [SHOPPER: A Probabilistic Model of Consumer Choice with Substitutes and Complements](https://arxiv.org/pdf/1711.03560)
- [NEAT: Label Noise-resistant Complementary Item Recommender](https://arxiv.org/pdf/2202.05456)
- [Function-based Labels for Complementary Recommendation: LLM-as-a-Judge](https://arxiv.org/pdf/2507.03945)

**Narrative extraction / multi-doc selection**
- [Connecting the Dots Between News Articles (Shahaf & Guestrin, KDD 2010)](https://www.researchgate.net/publication/220813703_Connecting_the_Dots_Between_News_Articles)
- [Metro Maps of Information (Shahaf, Guestrin, Horvitz, WWW 2012)](https://www.hyadatalab.com/papers/shahaf-maps.pdf)
- [A Survey on Event-Based News Narrative Extraction (ACM CSUR 2023)](https://arxiv.org/pdf/2302.08351)
- [Narrative Trails: Coherent Storyline Extraction via Maximum Capacity Path (2025)](https://arxiv.org/html/2503.15681v1)
- [NarraSum: Large-Scale Abstractive Narrative Summarization (EMNLP 2022)](https://arxiv.org/html/2212.01476)

**LLM rerankers / selectors**
- [Is ChatGPT Good at Search? (RankGPT)](https://arxiv.org/abs/2304.09542)
- [A Setwise Approach for Zero-shot LLM Ranking (SIGIR 2024)](https://dl.acm.org/doi/10.1145/3626772.3657813)
- [JointRank: Rank Large Set with Single Pass](https://arxiv.org/html/2506.22262v1)
- [AcuRank: Uncertainty-Aware Adaptive Listwise Reranking](https://arxiv.org/pdf/2505.18512)
- [Guiding Retrieval using LLM-based Listwise Rankers](https://arxiv.org/pdf/2501.09186)
- [The Evolution of Reranking Models: Heuristics to LLMs (survey)](https://arxiv.org/pdf/2512.16236)
- [The Case Against LLMs as Rerankers (Voyage AI)](https://blog.voyageai.com/2025/10/22/the-case-against-llms-as-rerankers/)

**Products / editorial automation**
- [NotebookLM — reverse-engineering the audio-overview system prompt](https://nicolehennig.com/notebooklm-reverse-engineering-the-system-prompt-for-audio-overviews/)
- [PodAgent: A Comprehensive Framework for Podcast Generation](https://arxiv.org/html/2503.00455)
- [Public Service Algorithm: LLM content curation on editorial values](https://arxiv.org/html/2506.22270)
- [LLM-Assisted News Discovery in High-Volume Streams (case study)](https://arxiv.org/html/2509.25491v1)

**Evaluation**
- [PodEval: Multimodal Evaluation for Podcast Audio Generation](https://arxiv.org/abs/2510.00485)
- [LLM-as-a-Judge as Proxies for Mathematical Coherence in Narrative Extraction (2025)](https://www.mdpi.com/2079-9292/14/13/2735)

## 4. How this meets the current codebase
- Buckets = `source.topics` (live). Embeddings = `source_embedding` chunks over
  clean_text (live; mean-pool for doc vectors). Entities = `metadata.key_entities`.
- Stage-2 relation typing is a new small module + a `source_relation` table
  (seed_id, candidate_id, relation, confidence, version) — the same
  provenance/versioning pattern as `topics`.
- Slots exactly where `cluster_sources` is being removed.
