# Optimization: Ranked, Cluster-Gated GNews Fetching

## Problem

The fetch step (`app/services/news_service.py`) queried every active interest independently. A "feed"-type IAB category like "Technology & Computing" alone fired 3 separate GNews RSS requests (one per underlying `TOPIC_CONFIGS` sub-topic), each a plain OR-of-keywords search capped at 50 results. Across 5 interests this produced ~250+ raw articles, all handed (after dedup/truncation to 150) to a single LLM call that had to sift signal from noise to pick a top-5. There was no fetch-time precision mechanism — pure OR search is high-recall/low-precision by construction.

Separately, the "Local Pulse" slot pulled from Google's generic geo-feed (`/rss/headlines/section/geo/{location}`), which has no concept of the user's actual interests — it had repeatedly surfaced crime/violence stories (e.g. "CBI books a case in Indian Bank fraud... Mumbai") that are bad content for a daily-brief local slot.

## Key finding (live-tested against the real GNews RSS endpoint)

Google News search honors `(OR-group) (OR-group)` as an implicit AND:

- **Thematically adjacent pairs** (tech+business, tech+music, business+music, careers+education, real-estate+home-garden, movies+television) return genuine cross-topic intersection articles — free precision gain over plain OR search.
- **Unrelated pairs** (tested: religion+semiconductors) do *not* return zero results — Google backfills with keyword-collision garbage (`"faith"` matching the market-sentiment idiom, `"chips"` matching "fish & chips" / "blue chips"), which is *noisier* than plain OR search, not less.

So combination has to be gated by curated, verified topical adjacency — never applied blindly to every active interest, and never applied by blindly cross-producting a composite category's sub-topics against a partner (caught during implementation: "Technology & Computing" × "Music and Audio" would correctly surface an AI×Music query but would *also* mechanically fire Semiconductors×Music and Big Tech×Music — pairings with no real adjacency).

## What changed

### 1. Top-5 interests only
`UserProfile.interests` is an ordered list; position = priority rank (index 0 highest). Only `interests[:5]` are ever queried — anything ranked below #5 gets zero requests. Product decision, not a technical limitation.

### 2. Curated IAB-category compatibility clusters
```python
IAB_COMPATIBILITY_CLUSTERS = [
    {"Technology & Computing", "Business and Finance"},
    {"Movies", "Television", "Music and Audio", "Events, Attractions & Pop Culture"},
    {"Real Estate", "Home & Garden"},
    {"Careers", "Education"},
    {"Style & Fashion", "Shopping"},
    {"Family and Relationships", "Healthy Living & medical health", "Religion & Spirituality"},
    {"Food & Drink", "Travel", "Hobbies & Interests"},
]
```
Interests sharing a cluster fire **one combined query per cross-product tuple** of their underlying GNews blocks instead of separate individual queries — exclusive, not additive: clustered members never also get an individual query.

A caught bug worth flagging: "Cybersecurity & Privacy" and "India Tech & Startups" were initially included in the tech cluster, but neither is an actual selectable IAB category — they're internal `TOPIC_CONFIGS` sub-topics used only inside "Technology & Computing"'s own block list. Including them meant `_combinable_blocks()` fell into the unmapped-fallback path and searched for the literal text `"Cybersecurity & Privacy"` instead of real keywords. Removed from the cluster table.

### 3. Technical constraint: not everything can combine
Some `TOPIC_CONFIGS` entries are Google's own curated topic-code sections (`/rss/headlines/section/topic/{CODE}`), not keyword searches — there's no text to AND:

- `Science` → `SCIENCE`, `Sports` → `SPORTS`, `News and Politics` → `WORLD` + `POLITICS`, and the `BUSINESS`-code half of `Business and Finance`.

These always fire as solo topic-feed requests regardless of clustering, and never get a location-folded variant.

### 4. No blind cross-producting
"Technology & Computing" is the only category with more than one GNews block (AI/ML, Semiconductors, Big Tech). Rule: a multi-block category may only cluster with a partner where *every* one of its blocks is independently defensible against that partner — hence it only clusters with "Business and Finance" (VC/funding is plausible against all three tech angles), never with narrow single-block domains like Music, where only the AI block would actually apply.

### 5. Article cap scales with query count
```
per_query_cap = max(10, min(100, (IAB_CATEGORY_ARTICLE_CAP * n) // p))
```
where `n` = number of clustered categories, `p` = number of cross-product queries fired. Preserves roughly the same aggregate per-category budget, spread across however many queries a cluster actually issues. Floored at 10, ceilinged at 100 (GNews RSS never returns more than 100 items per request — observed directly during testing).

### 6. Local geo-feed replaced entirely
`/rss/headlines/section/geo/{location}` is gone. Every combined/solo search query gets an additional location-folded variant (`(query) ("{location}")`), tagged `Local: {location}` exactly as before so the existing downstream Local Pulse slot logic (`prompts.py`, `pipeline.py`) needed no changes. Verified for Mumbai: no more crime stories — results are now gated by the user's actual interests instead of "anything published about this city."

### 7. Ranking made visible in the UI
`static/app.js` / `static/styles.css`: interest chips now show a numbered rank badge, ▲▼ reorder arrows (swap position without deselect/reselect), and dim any active chip ranked beyond #5 so it's clear at a glance which selections are actually live.

## Files touched
- `app/services/news_service.py` — cluster table, block resolution, grouping/cross-product query construction, cap formula, location fold-in.
- `static/app.js`, `static/styles.css` — rank badges, reorder controls, dimmed-exclusion styling.

## Verification
- Live-tested against the real GNews RSS endpoint (not mocked) for both the adjacency assumptions and the final implementation — Tech+Business cluster confirmed to surface genuine funding/VC intersection articles; Mumbai local variant confirmed clean of crime content.
- Caught and fixed the "Cybersecurity & Privacy" / "India Tech & Startups" phantom-category bug via an actual end-to-end fetch run, not just code review.
- Frontend served correctly (`app.js`/`styles.css` confirmed present in served output) and logic manually traced; **not** visually screenshot-verified in a real browser — no `chromium-cli`/Playwright available in this environment.

## Unaffected
Discovery Slot logic, `MAX_ARTICLES_FOR_SCORING` downstream ceiling, `pipeline.py`, `prompts.py`, `scoring_service.py`, and the article shape/`"Local:"`-prefix convention consumed downstream.

---

## Appendix: exact code changes (for reverting)

These three files already had unrelated pending edits before this optimization (per `git status`), so a blanket `git checkout` on them would also discard that other work. Below is the precise, surgical list of what this optimization itself added/removed, for manual reversal if it doesn't pan out.

### `app/services/news_service.py`
- Added `import itertools` and `Optional` to the `typing` import.
- Added module-level constants (placed after `IAB_CATEGORY_ARTICLE_CAP`): `IAB_COMPATIBILITY_CLUSTERS`, `MAX_ACTIVE_INTERESTS = 5`, `MIN_QUERY_CAP = 10`, `MAX_QUERY_CAP = 100`, `LOCAL_VARIANT_CAP = 20`.
- Added four new `NewsService` methods, placed just before `fetch_articles_for_profile`: `_combinable_blocks()`, `_topic_code_configs()`, `_cluster_id_for()`, `_combined_query_groups()`.
- Inside `fetch_articles_for_profile()`:
  - **Removed** the old "1. Fetch interest news" loop — the one that iterated the full `interests` list and, for each, fired one query per `IAB_TO_GNEWS`/`TOPIC_CONFIGS` sub-topic independently.
  - **Removed** the old "2. Fetch local geographic news" block — the direct call to `/rss/headlines/section/geo/{location}`.
  - **Added** in their place: `active_interests = interests[:MAX_ACTIVE_INTERESTS]`, a solo topic-code request loop (via `_topic_code_configs`), and a combinable-groups loop (via `_combined_query_groups`) that fires cross-product AND queries plus a location-folded variant per query.
  - **Changed** the Discovery Slot's `used_topic_codes` computation to derive from `active_interests` via `_topic_code_configs()` instead of inline logic over the full `interests` list.
- To revert: delete the added constants/methods, restore the original two numbered blocks (still visible in git history / this file's prior version before this session), and change `used_topic_codes` back to iterating the full `interests` list with the original inline logic.

### `static/app.js`
- Added `const MAX_ACTIVE_INTERESTS = 5;`.
- Added three new functions: `setupChipRankControls()`, `moveInterestRank()`, `renderInterestRanks()`.
- In `init()`: added `setupChipRankControls();` and `renderInterestRanks();` calls right after the existing "sync initial interests from active chips" block.
- In the interest-chip click handler (`elements.interestChips.forEach(...)`): added a `renderInterestRanks();` call at the end of the listener.
- In `loadPastRunDetails()`: added a `renderInterestRanks();` call right after the existing "Match active chips" block.
- To revert: delete the three added functions and the constant, and remove the three added call sites (the rest of each function reverts to its original body).

### `static/styles.css`
- Extended the existing `.chip.active` rule with `display: inline-flex; align-items: center; gap: 0.35rem;`.
- Added new rules after it: `.chip.active.chip-excluded`, `.chip.active.chip-excluded:hover`, `.chip-rank-badge`, `.chip.active.chip-excluded .chip-rank-badge`, `.chip-rank-badge:empty`, `.chip-rank-arrows`, `.chip-rank-arrow`, `.chip-rank-arrow:hover`.
- To revert: remove the three added `display/align-items/gap` properties from `.chip.active`, and delete every rule listed above.
