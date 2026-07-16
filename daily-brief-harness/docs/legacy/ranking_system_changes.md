# Ranking System — Change Log

Three changes to the system described in `no_llm_article_ranking.md`, based on observed results.

---

## Change 1 — Deduplication threshold and same-source collapse

**Problem:** Four near-identical AWS FDE articles all appeared in the top 18. The `difflib` threshold of `0.65` missed them because the headlines are syntactically different enough ("AWS puts $1B", "Amazon's AWS commits $1B", "Amazon launches new $1B FDE org") despite being the same story.

**Fix:** Lower the similarity threshold to `0.50`, and add a secondary same-source collapse rule — if two articles share the same source domain and their first six words overlap, treat them as duplicates regardless of the similarity score.

```python
from difflib import SequenceMatcher

def deduplicate(articles: list[dict], threshold: float = 0.50) -> list[dict]:
    seen_titles = []
    seen_short_keys = set()
    unique = []

    for article in articles:
        title = article["title"]
        source = article["source"].lower()

        # Secondary key: source + first 6 words of title
        short_key = source + " " + " ".join(title.lower().split()[:6])

        is_duplicate = (
            short_key in seen_short_keys
            or any(
                SequenceMatcher(None, title.lower(), seen.lower()).ratio() > threshold
                for seen in seen_titles
            )
        )

        if not is_duplicate:
            seen_titles.append(title)
            seen_short_keys.add(short_key)
            unique.append(article)

    return unique
```

**Why 0.50 and not lower:** Going below 0.50 risks collapsing genuinely distinct stories that share common words (e.g. two different TSMC articles in the same week). The same-source secondary key handles the remaining gap — two articles from the same outlet covering the same event will almost always share their first six words.

---

## Change 2 — Roundup article pattern in story type scoring

**Problem:** "AMD, Sandisk, AeroVironment, Air Products, Strategy, and More Stocks That Explain Today's Market" scored 5.91 and ranked in the top 18. It mentioned AMD and Sandisk so the embedding placed it near the Semiconductors anchor, but it is a shallow daily market summary covering 6 unrelated companies with one sentence each — no depth, no novel information.

**What a roundup is:** A roundup is a financial journalism format that bundles several unrelated companies into a single article under a framing like "stocks that explain today's market" or "biggest analyst calls." They look topically relevant because they name-drop major companies, but they carry no real signal about any individual company.

**Fix:** Add a roundup detection pattern at the top of `STORY_TYPES` with a score of `0.15`. This must be checked before the other patterns, since roundup headlines also contain company names that would otherwise match higher-scoring patterns.

```python
STORY_TYPES = [
    # --- add this block at the top, before all other patterns ---
    (
        r"and more stocks|stocks?\s+that\s+(explain|drove|move)\s+today"
        r"|biggest\s+(analyst\s+)?calls?:"
        r"|market\s+(movers?|roundup|brief|wrap)"
        r"|explain(s)?\s+today.s\s+market",
        0.15
    ),
    # --- existing patterns follow unchanged ---
    (
        r"\blaunches?\b|\bintroduces?\b|\bunveils?\b|\bnow available\b|"
        r"generally available|debuts?\b|releasing\b",
        0.90
    ),
    (
        r"raises?\s+\$[5-9]\d{2}[Mm]|raises?\s+\$[1-9][0-9]*[Bb]|"
        r"\$[1-9][0-9]*\s*billion",
        0.85
    ),
    (
        r"\bcancels?\b|\bstalls?\b|\braided?\b|\bcrackdown\b|"
        r"\bprobe\b|\bsmuggling\b|\bshuts?\s+down\b|\bdrops?\b",
        0.80
    ),
    (
        r"raises?\s+\$[1-9]\d{1}[Mm]|\bacquires?\b|\bpartnership\b|"
        r"signs?\s+deal\b|\bmerger\b",
        0.65
    ),
    (
        r"\bIPO\b|\bvaluation\b|\bseries\s+[ABC]\b",
        0.60
    ),
    (
        r"\bsurges?\b|\bjumps?\b|\bfalls?\b|\bslips?\b|52-week|"
        r"record high\b|\bstock up\b|\bstock down\b",
        0.30
    ),
    (
        r"\bopinion\b|\bwhy you should\b|\breasons? to buy\b|"
        r"could hit \$|\bshould i\b",
        0.20
    ),
]
```

**Other roundup signals to watch for:** "Here are Monday's biggest analyst calls: Nvidia, Apple, Tesla..." (CNBC runs these daily) and "[X], [Y], [Z] and More Stocks" (Barron's). Both follow the pattern above and will be caught by the regex.

---

## Change 3 — Dynamic active topics instead of hardcoded core/penalty sets

**Problem:** The previous design loaded all 11 `TOPIC_ANCHORS` and computed similarity against all of them, then used hardcoded `CORE_TOPICS` and `OFF_TOPIC_PENALTY_TOPICS` sets to adjust the score. This was fragile — "Anthropic launches Claude Sonnet 5" had its TS score pulled down because it also scored well against the "Tech Giants" anchor.

**Root cause:** The penalty logic was compensating for a design flaw. If only the active topics for a given run are used during scoring, the competition from irrelevant anchors does not exist in the first place.

**Fix:** Pass `active_topics` as a parameter at scoring time. Pre-compute embeddings for all anchors once at startup, then slice to the relevant subset per run.

```python
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

model = SentenceTransformer("all-MiniLM-L6-v2")

# Pre-compute all anchor embeddings once at startup
anchor_embeddings = {
    topic: model.encode(text)
    for topic, text in TOPIC_ANCHORS.items()
}


def topic_similarity(article: dict, active_topics: list[str]) -> float:
    emb = model.encode(f"{article['title']} {article['source']}")
    scores = {
        topic: float(cosine_similarity([emb], [anchor_embeddings[topic]])[0][0])
        for topic in active_topics  # only score against topics active this run
    }
    return max(scores.values())


def score_article(article: dict, active_topics: list[str]) -> float:
    if hard_negative_match(article):
        return 0.0
    if not source_allowed(article):
        return 0.0

    ts = topic_similarity(article, active_topics)

    s_type = story_type_score(article["title"])
    s_ent  = entity_tier_score(article["title"])
    s_dol  = dollar_score(article["title"])
    rs = 0.4 * s_type + 0.4 * s_ent + 0.2 * s_dol

    return ts * 0.55 + rs * 0.45


def rank_articles(
    articles: list[dict],
    active_topics: list[str],
    top_n: int = 30
) -> list[dict]:
    articles = deduplicate(articles)

    for article in articles:
        article["score"] = score_article(article, active_topics)

    return sorted(articles, key=lambda x: x["score"], reverse=True)[:top_n]
```

**Usage across runs:**

```python
# Run 1 — current use case
results = rank_articles(articles, active_topics=[
    "AI & Machine Learning",
    "Semiconductors & Hardware",
    "Startups & Venture Capital",
    "India Tech & Startups",
])

# Run 2 — sports only
results = rank_articles(articles, active_topics=["Sports"])

# Run 3 — sports and politics
results = rank_articles(articles, active_topics=["Sports", "Politics"])
```

`TOPIC_ANCHORS` is now just a library. Any subset of its keys can be passed as `active_topics` without changing any scoring logic. No penalty sets, no hardcoded core topic lists.
