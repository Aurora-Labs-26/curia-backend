# Ranking System — Change Log 2

Three changes on top of `ranking_system_changes.md`, based on observed results.

---

## Change 1 — Normalise topic similarity scores

**Problem:** All TS scores are compressed into a narrow band (3.7–5.8 out of 10), with no meaningful separation between a perfect match and a mediocre one. "Anthropic debuts Claude Sonnet 5" scored 3.8 while the AMD roundup (junk) scored 5.7. The root cause is that `all-MiniLM-L6-v2` cosine similarities naturally cluster in roughly the 0.25–0.60 range for most text pairs. A linear scale to 0–10 puts everything in the middle.

**Fix:** Rescale the raw cosine output from its natural range to 0–1 before multiplying by 10.

```python
def normalize_ts(raw: float, floor: float = 0.20, ceiling: float = 0.60) -> float:
    """Rescale cosine similarity from its natural range [floor, ceiling] to [0, 1]."""
    return max(0.0, min(1.0, (raw - floor) / (ceiling - floor)))


def topic_similarity(article: dict, active_topics: list[str]) -> float:
    emb = model.encode(f"{article['title']} {article['source']}")
    scores = {
        topic: float(cosine_similarity([emb], [anchor_embeddings[topic]])[0][0])
        for topic in active_topics
    }
    raw = max(scores.values())
    return normalize_ts(raw) * 10   # scale to 0–10 to match RS
```

**Calibrating floor and ceiling:** Run this diagnostic on your actual article set before fixing the values.

```python
# Print raw cosine scores for a labelled sample
samples = [
    ("Anthropic launches Claude Sonnet 5", "Anthropic", "relevant"),
    ("Nvidia Vera Rubin architecture", "SiliconANGLE", "relevant"),
    ("AMD, Sandisk, AeroVironment and more stocks", "Barron's", "junk"),
    ("North Dakota opens wellness equipment funding", "HHS North Dakota", "junk"),
    ("Kingston-Ulster Airport gets $474K in federal funding", "Daily Freeman", "junk"),
]

for title, source, label in samples:
    emb = model.encode(f"{title} {source}")
    for topic in active_topics:
        score = float(cosine_similarity([emb], [anchor_embeddings[topic]])[0][0])
        print(f"[{label}] {title[:40]} | {topic[:20]} | {score:.4f}")
```

Set `floor` just above the top of the junk cluster and `ceiling` at the bottom of the clearly-relevant cluster. Typical starting values for `all-MiniLM-L6-v2` are `floor=0.20` and `ceiling=0.60`, but verify against your own anchors.

---

## Change 2 — Hard cap composite score for roundup articles

**Problem:** The AMD roundup article ("AMD, Sandisk, AeroVironment, Air Products, Strategy, and More Stocks That Explain Today's Market") dropped to rank 10 and its Signif correctly shows 5.6, confirming the roundup story_type pattern is firing. However, its TS score (5.7) is high enough to compensate, keeping it alive in the results. A roundup that mentions AMD and Sandisk will always embed near the Semiconductors anchor regardless of how little signal it carries.

**Options:** Either cap the composite score when the roundup pattern fires, or move roundup patterns to `HARD_NEGATIVES` and eliminate them before scoring entirely. The second option is cleaner — there is no scenario where a roundup article belongs in the top results regardless of which companies it mentions.

**Preferred fix — add to `HARD_NEGATIVES`:**

```python
HARD_NEGATIVES += [
    # Roundup / analyst call digests
    r"and more stocks",
    r"stocks?\s+that\s+(explain|drove|move)\s+today",
    r"biggest\s+(analyst\s+)?calls?:",
    r"market\s+(movers?|roundup|brief|wrap)",
]

def hard_negative_match(article: dict) -> bool:
    text = (article["title"] + " " + article["source"]).lower()
    return any(re.search(neg, text) for neg in HARD_NEGATIVES)
```

**Alternative fix — composite cap (if you want to keep roundups visible but deprioritised):**

```python
def score_article(article: dict, active_topics: list[str]) -> float:
    if hard_negative_match(article): return 0.0
    if not source_allowed(article): return 0.0

    ts = topic_similarity(article, active_topics)
    s_type = story_type_score(article["title"])
    s_ent  = entity_tier_score(article["title"])
    s_dol  = dollar_score(article["title"])
    rs = 0.4 * s_type + 0.4 * s_ent + 0.2 * s_dol

    score = ts * 0.55 + rs * 0.45

    # Hard cap if roundup pattern fired
    if s_type == 0.15:
        return min(score, 4.0)

    return score
```

---

## Change 3 — Multi-topic boost: additive and capped

**Context:** A boost was added for articles that score above a similarity threshold on multiple active topics, reflecting that intersection articles (e.g. Etched: AI + Semiconductors + Startups) are more valuable than single-topic articles.

**One risk to avoid:** If the boost is multiplicative, an article that weakly hits three topics can outscore one that strongly matches one topic. The boost should be additive and capped so that weak multi-topic matches cannot game the system.

```python
def multi_topic_boost(
    article: dict,
    active_topics: list[str],
    threshold: float = 0.40,
    boost_per_topic: float = 0.30,
    max_boost: float = 0.60,
) -> float:
    """
    Returns an additive score boost for articles that hit multiple active topics
    above the similarity threshold. Capped at max_boost regardless of topic count.

    Example:
      - Hits 1 topic  → +0.0
      - Hits 2 topics → +0.3
      - Hits 3 topics → +0.6 (capped)
      - Hits 4 topics → +0.6 (capped)
    """
    emb = model.encode(f"{article['title']} {article['source']}")
    hits = sum(
        1 for topic in active_topics
        if float(cosine_similarity([emb], [anchor_embeddings[topic]])[0][0]) > threshold
    )
    return min((hits - 1) * boost_per_topic, max_boost) if hits > 1 else 0.0


def score_article(article: dict, active_topics: list[str]) -> float:
    if hard_negative_match(article): return 0.0
    if not source_allowed(article): return 0.0

    ts = topic_similarity(article, active_topics)
    s_type = story_type_score(article["title"])
    s_ent  = entity_tier_score(article["title"])
    s_dol  = dollar_score(article["title"])
    rs = 0.4 * s_type + 0.4 * s_ent + 0.2 * s_dol

    score = ts * 0.55 + rs * 0.45 + multi_topic_boost(article, active_topics)

    if s_type == 0.15:
        return min(score, 4.0)

    return score
```

**Tuning notes:**
- `threshold=0.40` is the raw cosine value, not the normalised TS. Keep it in raw cosine space so it is independent of the floor/ceiling calibration in Change 1.
- If intersection articles are still underweighted after this, raise `boost_per_topic` to `0.5` before touching the composite weights.
- If shallow multi-topic matches are gaming the results, raise `threshold` to `0.45` or `0.50`.
