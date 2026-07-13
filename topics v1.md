# Topics v1 — Source Bucketing (pins + LLM)

**Branch:** `v2.9`
**Date:** 2026-07-13
**Authors:** Arihant + Claude (claude-fable-5)
**Status:** Design — supersedes the earlier "iab tagging v1" draft after design review

---

## The Problem

Curia has no usable answer to "what is this source about?" The only categorization
today is a 12-label `category` field buried inside the `metadata` transformation's
JSON, stored as a string in `source_insight` — unindexed, unfilterable, and consumed
by nothing except a debug display.

We want every source tagged into a fixed two-tier bucket taxonomy so sources can be
filtered, routed, and analyzed by topic.

---

## Decisions Locked (design sessions, 2026-07-13)

| Decision | Choice |
|---|---|
| Name | **`topics`** — new JSONB column on `source` |
| Taxonomy | IAB Content Taxonomy (Tier 1 → Tier 2), **Pets excluded**, plus 4 custom Tier-1s for Curia's essay corpus |
| Output shape | ≤ 3 Tier-1 per source, ≤ 2 Tier-2 per Tier-1 |
| Architecture | **Two layers only: deterministic pins + LLM judge.** Keyword/token matching was cut in review (string-matching noise, hand-curated synonym rot, zero information the LLM doesn't already have) |
| Confidence | **Per-tag** (`src` on every tag), not per-source. Pinned tags are never re-litigated by the LLM |
| Old 12-label `category` | **Deleted** from the metadata prompt. `topics` is the only topical field; `metadata` keeps only formal facts (author, publication, type, tone, length_bucket, key_entities, approx_year, language) |
| Empty result | `{"tags": []}` is a valid terminal state (honestly untaggable). `NULL` = not yet classified |

Rejected in review, deliberately: keyword index over label tokens; LLM "hints"
(noisy candidates in the prompt); source-level confidence gate with score
thresholds; taxonomy registry / swappable-taxonomy plumbing.

---

## Taxonomy

Base: the 28 IAB Tier-1 categories with their Tier-2 lists as provided
(Automotive … Video Gaming), **Pets excluded**. Canonical copy lives in code:
`core/taxonomy/buckets.py::TAXONOMY`.

Extension — 4 custom Tier-1s (IAB has no home for Curia's longform-essay corpus;
these carry `custom: true` for any future strict-IAB export):

| Custom Tier-1 | Tier-2 |
|---|---|
| **Philosophy** | Ethics & Morality, Epistemology & Rationality, Political Philosophy, Philosophy of Mind, Metaphysics, Applied Philosophy |
| **Psychology & Self** | Cognitive Science, Behavioral Psychology, Mental Models & Decision-Making, Productivity & Habits, Emotions & Wellbeing, Social Psychology |
| **History** | Ancient History, Modern History, History of Science & Technology, Economic History, Military History, Biographical History |
| **Media & Journalism** | Journalism & Publishing, Social Media & Platforms, Internet Culture, Advertising & Attention Economy, Creator Economy |

Label strings are frozen identifiers — renaming one requires migrating stored rows.
Every label also gets a stable numeric ID (Tier-1 `25`, Tier-2 `25.1`) used in the
LLM wire format; IDs never change even if display labels are ever touched.

---

## Architecture

```
            Layer 1: deterministic PINS (publisher-declared facts only)
            ─────────────────────────────────────────────────────────────
url ───►    hostname  → domain pin        espn.com → Sports
            path seg  → section pin       theguardian.com/sport/ → Sports
                                          (section beats domain on conflict)
                        │
              pins complete? (Tier-1 AND Tier-2 both pinned,
              e.g. espn.com/soccer/ → Sports > Soccer)
                        │
              yes ──────┴────── no / partial / none
               │                     │
               ▼                     ▼
        store pins,          Layer 2: LLM judge (one haiku call)
        skip LLM             taxonomy(IDs) + url/title/text + pins-as-constraints
                                     │
                             validate (IDs → labels, caps, Tier-2 ∈ Tier-1)
                                     │
                        ┌────────────┼──────────────┐
                    valid tags   valid EMPTY     LLM error
                    store        store {tags:[]} store pins if any,
                                 (kept as-is)    else leave NULL → retry
```

### Layer 1 — Pins (deterministic)

A pin is a Tier-1 (optionally with Tier-2) asserted by **publisher-declared
structure**, never by content string-matching:

- **Domain pin** — hostname lookup in the `domain_pins` table (seeded by a small
  hand-curated list: espn→Sports, techcrunch→Technology & Computing, …).
  Hostname includes subdomain, so `astralcodexten.substack.com` can pin
  independently of substack.com.
- **Section pin** — exact match of a URL path segment against a small curated
  slug map (~20 entries, only unambiguous ones): `/sport/`, `/sports/`, `/tech/`,
  `/technology/`, `/politics/`, `/business/`, `/science/`, `/health/`, `/food/`,
  `/travel/`, `/music/`, `/movies/`, `/film/`, `/books/`, `/style/`,
  `/realestate/`, plus a few Tier-2-grade slugs (`/soccer/`, `/nba/` → Sports >
  Basketball). Sections are the publisher's own categorization of that specific
  article — near-zero false positives, and they beat domain pins on conflict.
- **YouTube** — the URL carries no topical signal; the channel (from `source.data`)
  acts as the hostname for pinning purposes (see learned pins).

Pins are **constraints**: they are in the final output unconditionally, and the
LLM is told they are confirmed. The known cost: espn.com publishing a pure-politics
op-ed still carries a Sports pin (the LLM adds the correct tags alongside).
Accepted for v1; learned-pin agreement thresholds keep systematic errors out.

### Layer 2 — LLM judge

One `dspy.Predict` on a **`classify.topics`** binding (haiku-4-5) in
`config/models.yaml`, prompt file-editable via `with_prompt(sig, "classify_topics")`.

Runs for every source **except** when pins already specify both tiers. In v1 that
means ~every source pays one haiku call — accepted: it is +1 call on top of the 7
existing per-source transformation calls (~14% ingest LLM cost), and the learned-pin
loop shrinks it over time.

The prompt contains exactly three things — no candidates, no hints, nothing derived:

1. **Taxonomy** (static, first — prompt-cache friendly), rendered with IDs:
   `25 Technology & Computing: 25.1 Artificial Intelligence, 25.2 Augmented Reality, …`
2. **Evidence, raw:** url, title, text signal
3. **Pins, as constraints:** *"Sports (25) is confirmed via source structure. Assign
   its Tier-2 if clear, then judge what else independently applies."*

Rules in the prompt: ≤3 Tier-1 by relevance, ≤2 Tier-2 each, precision over
coverage, **an empty array is a correct answer**, output JSON only, IDs only:

```json
[{"t1": 25, "t2": ["25.1"]}, {"t1": 29, "t2": []}]
```

Few-shot: 2–3 fixed examples including one whose correct answer is `[]` (licenses
the model to say "no bucket fits" instead of force-fitting).

**Why IDs, not labels:** ~60% fewer output tokens, no typo/paraphrase drift
("Tech & Computing"), and validation is a dict lookup instead of fuzzy matching.

Validation in code: unknown IDs dropped, Tier-2 must belong to its Tier-1, caps
enforced, pins merged in. Injection via scraped titles is bounded by this layer —
worst case is a wrong tag, never a novel label.

### Signals

- **url** — as saved. Skipped as LLM evidence for `source_type='youtube'`.
- **title** — scraper page title; for YouTube, channel name is appended.
- **text signal** — `summary` primitive when present, else first 1,500 chars of
  `full_text` (no hard dependency on a primitive that `revamp v1.md` may drop;
  switches to `clean_text` when that lands).

---

## Storage

Migration **`0031_source_topics`** (0029 = og_image, 0030 = source_type):
`topics JSONB NULL` on `source` + GIN index.

```json
{
  "version": 1,
  "tags": [
    { "tier1": "Sports",               "tier2": ["Basketball"], "src": "section" },
    { "tier1": "Business and Finance", "tier2": ["Business"],   "src": "llm" }
  ]
}
```

- `src` per tag: `domain | section | learned | llm`. No envelope-level method —
  confidence is a per-tag fact.
- `version` — classifier logic version; bump on taxonomy/prompt changes, re-classify
  targeted (`WHERE topics->>'version' < 'N'`).
- `NULL` = not yet classified (incl. LLM error with no pins — natural retry).
  `{"tags": []}` = classified, fits nothing.

Bucket query: `WHERE topics -> 'tags' @> '[{"tier1": "Sports"}]'`

### `domain_pins` table (same migration)

```
domain_pins
├─ hostname      TEXT PRIMARY KEY      -- or "yt:<channel>" for YouTube
├─ tier1         TEXT NOT NULL
├─ tier2         TEXT NULL
├─ origin        TEXT NOT NULL         -- 'seed' | 'learned'
├─ sample_count  INT                   -- evidence behind a learned pin
├─ agreement     REAL                  -- LLM agreement ratio at promotion
└─ created_at    TIMESTAMPTZ
```

---

## Making Each Layer Better

### Deterministic: learned pins (the self-improving loop)

The deterministic layer should not be a hand-curated list that rots — it should be
a **distillation of the LLM's own consensus**:

> Periodic job: for every hostname (or YouTube channel) with ≥ **10** classified
> sources where ≥ **90%** share the same Tier-1 (`src='llm'`), insert a
> `domain_pins` row with `origin='learned'`.

- Hand-curated hints become mere **seed data**; coverage grows with the corpus
- Substack authors and YouTube channels get pinned automatically once a user has
  saved enough of them — precisely the sources users save repeatedly
- Every promotion permanently converts future LLM calls into free classifications —
  the cascade's cost savings are *earned from evidence* instead of guessed
- Safety: thresholds keep mixed-topic hosts (medium.com, substack.com apex,
  youtube.com itself) from ever qualifying; a misfiring learned pin is revoked by
  deleting its row

### LLM: measured, then optimized

1. **Eval set first** — ~100 hand-labeled real sources; report Tier-1
   precision/recall, Tier-2 accuracy given correct Tier-1, % empty, pin precision
   (any wrong pin ⇒ fix the seed list / raise promotion thresholds). Ship threshold:
   Tier-1 precision ≥ 0.85 before anything downstream consumes tags.
2. **GEPA/MIPRO on the classify signature** — the judge is a DSPy signature and the
   repo already has the optimization harness (`optimization/`); once the eval set
   exists, the prompt is tuned against measurement instead of intuition.
3. **Batch mode for backfill** — classify N sources per call (shared taxonomy
   prefix) when sweeping the existing corpus; per-source at ingest stays single.
4. **Prompt-cache alignment** — static taxonomy+rules prefix, per-article suffix.

---

## Integration (v2.9 `process_source`)

Step **2b**, between the transformation loop and embedding:

1. SELECT gains `title` (branch already fetches `source_type`)
2. Transform loop captures outputs into a dict (for the summary text-signal)
3. `classify_source(url, title, text_signal, source_type, channel)` via
   `run_in_executor`; writes the envelope to `topics`
4. try/except — tagging failure logs `✗ topics`, never fails the ingest
5. `source_ingested` PostHog event gains `"topics_tier1": [...]` and
   `"topics_src": [...]` — free category-level ingest analytics

Plus the one-field deletion: **`category` removed from the metadata prompt**
(`core/prompts/transformations.py:66`) — `metadata` becomes purely formal
(author, publication, type, tone, length_bucket, key_entities, approx_year,
language). Zero functional consumers today (verified by grep; only a debug-UI
display chip, updated to read `topics`).

Untouched: the other 6 transformations, embeddings, clustering, briefing, episode
generation.

---

## Post-Ingestion Schema (one article, final)

```
source
├─ id / user_id / url / status / pool / is_seed / timestamps
├─ title, author, og_image, source_type, full_text   ← scraper
├─ data        JSONB   scraper extras (YT: video_id, duration, views…)
└─ topics      JSONB   ← this design (envelope above)

source_insight (7 rows)
├─ summary          argument summary (text-signal source for topics)
├─ metadata         8 formal keys — category DELETED
├─ key_insights / human_stakes / core_tensions / counterpoints / examples

source_embedding (~N chunk rows) · source_primitive_embedding (1 row)
```

Division of labor: **topics** = what it's about · **metadata** = who/what form ·
**insights** = what it says.

---

## Known Limitations (accepted v1)

- Pins depend on seed coverage until learned pins accumulate; unknown domains just
  take the LLM path (correct, not cheaper).
- Off-topic articles on pinned domains carry the domain's pin alongside correct tags.
- No multi-language special-casing — the LLM handles non-English; pins are
  language-agnostic (structural).
- No backfill by default; `WHERE topics IS NULL` sweep script when wanted.

---

## File Map & Implementation Order

| # | File | New/Edit | Content |
|---|---|---|---|
| 1 | `core/taxonomy/buckets.py` | new | TAXONOMY (+IDs, custom flags), SEED_DOMAIN_PINS, SECTION_SLUGS, validation helpers |
| 2 | `core/taxonomy/classify.py` | new | pin resolution, DSPy judge (ID wire format), validator, envelope, `classify_source()` |
| 3 | `core/taxonomy/__init__.py` | new | public surface |
| 4 | `alembic/versions/0031_source_topics.py` | new | `topics` column + GIN; `domain_pins` table |
| 5 | `config/models.yaml` | edit | `classify.topics: haiku-4-5` binding |
| 6 | `core/prompts/transformations.py` | edit | delete `category` from metadata prompt |
| 7 | `core/ingest.py` | edit | step 2b + analytics props |
| 8 | `api/routes/eval.py` | edit | display chip reads `topics` |
| 9 | `tests/test_topics_classify.py` | new | mock-only: pin resolution (domain/section/conflict/yt), ID validation, empty-vs-error states, envelope |
| 10 | `scripts/eval_topics.py` | new | Phase 2 — eval harness |
| 11 | `scripts/promote_pins.py` | new | Phase 3 — learned-pin promotion job |
| 12 | `CHANGELOG.md` | edit | required entry |

**Phase 1:** 1–9 → tags flowing, analytics live.
**Phase 2:** 10 + hand-labeling → measured quality; GEPA the prompt.
**Phase 3:** 11 → learned pins shrink LLM cost; only then consider downstream use
(clustering signal per `revamp v1.md`, user-facing filters, backfill).

---

## What We're NOT Doing (v1)

- Keyword/token matching of any kind
- Feeding topics into clustering or briefing (blocked on eval)
- Multi-language keyword layers, taxonomy registries, Tier-3 depth
- Per-user or per-show taxonomy customisation
