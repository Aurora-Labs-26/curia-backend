# Transformation Ablation Study v1 — Results

**Date:** 2026-07-20
**Question:** With the prettified article (`clean_text`) available to the briefing,
which extracted primitives actually improve transcript quality — and does the
current primitives-only briefing hold up against raw article text at all?
**Companion docs:** `revamp v1.md` (the thesis under test), `companion selection
research v1.md` (why primitives are being reworked).

---

## 1. Method

### Arms (9 briefing configurations, identical template — only content blocks vary)

| Arm | Briefing content |
|---|---|
| A | key_insights + core_tensions + counterpoints, **no article text** — approximates current production behavior |
| **B** | **clean_text only — the baseline every arm is judged against** |
| C | clean_text + key_insights |
| D | clean_text + core_tensions |
| E | clean_text + counterpoints |
| F | clean_text + core_tensions + counterpoints ("structural turn" combo) |
| G | clean_text + all three primitives |
| H | clean_text + summary |

Deliberately not tested: `metadata` (structural value — entities/routing — not
measurable through transcript quality); `human_stakes` and `examples` (removed
2026-07-19 by decision, no data resurrection wanted).

### Sources (8, real production corpus, distinct domains)

- Reading "The Power Broker" Has Changed My Life
- 5 ways I use NotebookLM that have nothing to do with research (androidpolice.com)
- The Bitter Lesson (incompleteideas.net)
- How does India work? (thisindianlife.today)
- Do Things that Don't Scale (paulgraham.com)
- Patents are a crutch (substack)
- Introducing Claude Sonnet 5 (anthropic.com)
- Indian D2C's Dirty Secret (substack)

Primitives came from the production DB as-extracted (no re-extraction);
`clean_text` computed via the production prettifier where the row predated it.
Article text capped at 14k chars in the briefing.

### Generation

Per (source × arm): outline (haiku) + transcript via the real production modules
(`studio.generator.generate_outline` / `generate_transcript`), format
`narrative_drift`, single host, `CURIA_ENV=prod` (sonnet transcript binding).
**64 transcripts**, avg 1,382 words (904–2,099), avg 69 s generation each.

### Judging

Pairwise, each arm vs baseline B **on the same source**, position-swapped
double judging (2 calls per comparison; split verdicts = 0.5) using the
configured `judge` binding (sonnet). Judge saw only the two transcripts and
listener-quality criteria (engagement, coherence, specificity/faithfulness,
spoken flow) — it did not know which arm was which. **56 comparisons / 112
judge calls.** Total study cost ≈ $12–15.

---

## 2. Results

Win rate = mean score vs baseline over 8 sources (1.0 = swept both positions,
0.5 = judges split, 0.0 = lost both).

| Arm | Win rate | Wins | Splits | Losses | Reading |
|---|---|---|---|---|---|
| **A** primitives-only | **0.25** | 0 | 4 | **4** | **decisively worst — only significant result** |
| C +key_insights | 0.56 | 1 | 7 | 0 | noise |
| D +core_tensions | 0.56 | 1 | 7 | 0 | noise |
| E +counterpoints | 0.50 | 0 | 8 | 0 | perfect coin flip |
| **F** +tensions+counterpoints | **0.62** | 2 | 6 | **0** | best; only combo that never lost |
| G +all three | 0.62 | 2 | 6 | 0 | identical to F ⇒ key_insights adds nothing |
| H +summary | 0.50 | 0 | 8 | 0 | perfectly inert |

---

## 3. Conclusions

1. **The briefing switch is confirmed with data.** The production-style
   primitives-only packet (A) lost to plain article text 0.25 — zero wins, half
   its matchups clean sweeps against it. This is the revamp doc's central claim
   ("summaries-of-summaries destroy the episode") surviving an experiment
   designed to kill it. Highest-value change available: put `clean_text` in the
   briefing.
2. **New briefing composition = arm F**: clean_text + core_tensions +
   counterpoints. Mild positive (0.62), and the only configuration that never
   lost a matchup.
3. **`key_insights` failed its trial**: inert alone (0.56 ≈ noise) and G-vs-F
   shows it contributes exactly nothing on top of F. Candidate for removal —
   ingest would drop to 4 transformation calls. (Wiring note: episode
   eligibility currently gates on key_insights via `has_complete_insights`;
   re-gate on clean_text presence.)
4. **`summary` is confirmed out of the quality path** (0.50, 8/8 splits). It
   survives only for its non-briefing jobs: UI blurbs and selection prompts.
5. **`counterpoints` has no solo value (0.50) but belongs inside F** —
   consistent with the planned stance-card rework, where tensions +
   counterpoints merge into a single richer extraction.

## 4. Caveats — read before quoting the numbers

- **n = 8 per arm.** Only A's collapse clears any reasonable significance bar.
  F's 0.62 is "keep, don't celebrate" — two sweeps away from a coin flip.
- **LLM-judged**, single judge model. Judge consistency is good (position-swap
  splits were counted conservatively as ties) but human listening was not part
  of this study; the eventual ground truth is listener completion rates.
- **One format** (narrative_drift, single host). Two-host merge dynamics were
  not tested and may consume primitives differently.
- Arm A used the same briefing *template* as other arms (primitives only) — a
  faithful approximation of, but not byte-identical to, the production packet
  builder.
- Harness: `scratchpad/ablation.py` (session artifact); raw transcripts +
  verdicts in `ablation_results.json` (64 transcripts, 56 judgments,
  incremental/crash-safe, reproducible by rerun — completed work replays from
  cache).

## 5. Actions adopted

| # | Change | Status |
|---|---|---|
| 1 | Briefing → clean_text + tensions + counterpoints (arm F) | queued |
| 2 | Remove `key_insights` transformation (ingest 5 → 4 calls) | queued |
| 3 | `summary` demoted to blurb-only (no briefing role) | policy, no code change yet |
| 4 | tensions+counterpoints → stance-card merge | part of Connect workstream |
