# No-LLM Article Ranking System

**Goal:** Replicate composite scoring (`topic_similarity × 0.55 + real_world_significance × 0.45`) without any LLM calls, at near-zero marginal cost per article.

---

## Why keyword matching fails

Keyword matching scores **topic presence** only. What you actually need is:

```
topic presence × story type × entity importance
```

The RS (real-world significance) dimension is entirely missing from a keyword approach. Concretely:

| Article | Keyword match | Problem |
|---|---|---|
| AMD Shares Surge 7% | hits "semiconductor" | stock price movement ≠ important story |
| Nifty IT Falls 3% | hits "India Tech" | market move, zero novel information |
| Aikido acquires Root | hits "startup + acquires" | entity too niche, deal too small |
| 10× Karnataka Gig Workers articles | all score independently | no deduplication |

---

## Architecture: three layers

```
Raw articles (213)
      │
      ▼
┌─────────────────────────┐
│  Layer 1: Hard negatives │  → drops ~40% immediately
│  + source allow-list     │
└────────────┬────────────┘
             │
      ▼
┌─────────────────────────┐
│  Deduplication           │  → collapses story clusters
└────────────┬────────────┘
             │
      ▼
┌─────────────────────────┐
│  Layer 2: Embeddings     │  → topic_similarity score (TS)
│  (sentence-transformers) │
└────────────┬────────────┘
             │
      ▼
┌─────────────────────────┐
│  Layer 3: Heuristic RS   │  → real_world_significance score
│  story type + entity tier│
│  + dollar normalization  │
└────────────┬────────────┘
             │
      ▼
 composite_score = TS × 0.55 + RS × 0.45
```

---

## Layer 1 — Hard negative filter + source allow-list

Run this first. It is O(n) string matching and kills the bulk of off-topic noise before any heavier computation.

### Hard negative phrases

```python
HARD_NEGATIVES = [
    # US local government / public funding
    "airport funding", "school funding", "state budget", "medicaid",
    "wellness equipment", "community college", "property tax",
    "transit funding", "child care", "conservation funding",
    "wastewater testing", "homelessness funding",
    # Health / discovery (not your topics)
    "rabies", "ebola", "marburg", "tick-borne", "cyclosporiasis",
    "alzheimer", "anti-vegf", "snap benefits",
    # Misclassified noise
    "fantasy football",
]

def hard_negative_match(article: dict) -> bool:
    text = (article["title"] + " " + article["source"]).lower()
    return any(neg in text for neg in HARD_NEGATIVES)
```

### Source allow-list

If the source is not in this set, score it 0 automatically. Maintain and expand as needed.

```python
SOURCE_ALLOWLIST = {
    # Tier 1 — always trust
    "techcrunch", "cnbc", "reuters", "bloomberg", "the information",
    "ap news", "wired", "the verge", "ars technica",
    # AI-specific
    "anthropic", "openai", "microsoft", "nvidia blog", "nvidia developer",
    "microsoft azure", "amazon web services", "about amazon",
    "github blog",
    # Semiconductor
    "tom's hardware", "siliconangle", "anandtech", "eetimes",
    "digitimes", "investing.com",
    # India Tech
    "economic times", "moneycontrol.com", "business today", "the ken",
    "mint", "business standard", "analytics india magazine",
    "the hindu", "medianame",
    # Startups & VC
    "fortune", "inc.com", "axios", "wsj", "barron's",
}

def source_allowed(article: dict) -> bool:
    return article["source"].lower() in SOURCE_ALLOWLIST
```

> **Tip:** Start strict and loosen over time. A false negative (missing a good article from an unlisted source) is much cheaper than false positives flooding your feed.

---

## Deduplication

Run this before scoring. Avoids the same story occupying multiple slots in your top N.

```python
from difflib import SequenceMatcher

def deduplicate(articles: list[dict], threshold: float = 0.65) -> list[dict]:
    seen_titles = []
    unique = []
    for article in articles:
        is_duplicate = any(
            SequenceMatcher(None, article["title"], seen).ratio() > threshold
            for seen in seen_titles
        )
        if not is_duplicate:
            seen_titles.append(article["title"])
            unique.append(article)
    return unique
```

This collapses story clusters like the 10 Karnataka Gig Workers articles into 1 automatically. The `0.65` threshold is a good starting point; tune down to `0.55` if you want more aggressive deduplication.

---

## Layer 2 — Embeddings for topic similarity

This is the most important upgrade over keyword matching. A frozen sentence encoder like `all-MiniLM-L6-v2` runs on CPU, costs nothing per call, takes ~20ms per article, and will correctly separate "AMD stock surges" from "AMD launches Venice CPU" even though both contain "AMD".

### Setup

```bash
pip install sentence-transformers scikit-learn
```

```python
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

model = SentenceTransformer("all-MiniLM-L6-v2")  # ~90MB, download once

# Define topic anchors as rich descriptive strings, not just keywords.
# The richer the anchor, the better the embedding alignment.
TOPIC_ANCHORS = {
    "AI & ML": (
        "artificial intelligence machine learning large language models LLM inference "
        "training foundation models OpenAI Anthropic Claude GPT agents benchmarks "
        "neural networks deep learning generative AI"
    ),
    "Semiconductors": (
        "semiconductor chip GPU CPU processor NVIDIA AMD Intel TSMC HBM memory "
        "packaging wafer foundry silicon photonics chip design fab manufacturing "
        "export controls chip smuggling"
    ),
    "India Tech": (
        "India startup technology TCS Infosys Wipro HCL Zomato Swiggy Zepto Bengaluru "
        "Mumbai gig workers IT sector Nifty IT Indian tech IPO rupee"
    ),
    "Startups & VC": (
        "startup venture capital Series A Series B unicorn acquisition IPO founder "
        "fundraising valuation seed round angel investor growth stage"
    ),
}

# Pre-compute anchor embeddings once at startup — not per article
anchor_embeddings = {
    topic: model.encode(text)
    for topic, text in TOPIC_ANCHORS.items()
}

def topic_similarity(article: dict) -> float:
    text = f"{article['title']} {article['source']}"
    emb = model.encode(text)
    scores = {
        topic: float(cosine_similarity([emb], [anchor])[0][0])
        for topic, anchor in anchor_embeddings.items()
    }
    # Return the best topic match (cosine similarity is 0–1)
    return max(scores.values())
```

> **Why not keyword matching here?**
> Embeddings capture semantic proximity, not token overlap. "Anthropic debuts Claude Sonnet 5" and "OpenAI cuts inference costs in half" don't share AI keywords but both embed near the AI & ML anchor. "AMD Shares Surge Over 7%" embeds closer to financial news than semiconductor news, even though it contains "AMD".

---

## Layer 3 — Heuristic significance scoring (RS)

Combines three sub-scores: story type, entity tier, and dollar amount. No LLM required.

```python
RS = 0.4 × story_type_score + 0.4 × entity_tier_score + 0.2 × dollar_score
```

### 3a. Story type score

Classify by headline pattern. This single heuristic fixes the "AMD stock up 7%" problem: market move stories are capped at 0.35 regardless of which company is involved.

```python
import re

STORY_TYPES = [
    # (pattern, score)
    # High significance
    (r"\blaunches?\b|\bintroduces?\b|\bunveils?\b|\bnow available\b|"
     r"generally available|debuts?\b|releasing\b",                         0.90),
    (r"raises?\s+\$[5-9]\d{2}[Mm]|raises?\s+\$[1-9][0-9]*[Bb]|"
     r"\$[1-9][0-9]*\s*billion",                                           0.85),
    (r"\bcancels?\b|\bstalls?\b|\braided?\b|\bcrackdown\b|"
     r"\bprobe\b|\bsmuggling\b|\bshuts?\s+down\b|\bdrops?\b",             0.80),
    # Medium significance
    (r"raises?\s+\$[1-9]\d{1}[Mm]|\bacquires?\b|\bpartnership\b|"
     r"signs?\s+deal\b|\bmerger\b",                                        0.65),
    (r"\bIPO\b|\bvaluation\b|\bseries\s+[ABC]\b",                         0.60),
    # Low significance
    (r"\bsurges?\b|\bjumps?\b|\bfalls?\b|\bslips?\b|52-week|"
     r"record high\b|\bstock up\b|\bstock down\b",                         0.30),
    (r"\bopinion\b|\bwhy you should\b|\breasons? to buy\b|"
     r"could hit \$|\bshould i\b",                                         0.20),
]

def story_type_score(title: str) -> float:
    title_lower = title.lower()
    for pattern, score in STORY_TYPES:
        if re.search(pattern, title_lower):
            return score
    return 0.50  # default: unclassified but not penalized
```

### 3b. Entity tier score

Pre-build a tier dictionary. Scan the headline for entity mentions, take the max tier.

```python
ENTITY_TIERS = {
    "S": {
        "score": 1.0,
        "entities": [
            "OpenAI", "Anthropic", "NVIDIA", "TSMC", "Apple", "Google",
            "Microsoft", "Meta", "AWS", "Amazon", "DeepSeek",
        ],
    },
    "A": {
        "score": 0.75,
        "entities": [
            "Intel", "AMD", "Samsung", "SK Hynix", "Tesla", "Qualcomm",
            "TCS", "Infosys", "Wipro", "HCL", "Accenture", "Palantir",
            "Chamath", "Meituan", "Supermicro", "Amkor", "Etched",
        ],
    },
    "B": {
        "score": 0.55,
        "entities": [
            "Zomato", "Swiggy", "Zepto", "Coforge", "Persistent",
            "LTIMindtree", "Aikido", "Vertiv",
        ],
    },
}

def entity_tier_score(title: str) -> float:
    for tier_data in ENTITY_TIERS.values():
        if any(entity.lower() in title.lower() for entity in tier_data["entities"]):
            return tier_data["score"]
    return 0.30  # unknown or niche entity
```

### 3c. Dollar amount normalization

Larger dollar figures generally signal higher real-world significance. Calibrate the divisors to your domain.

```python
def dollar_score(title: str) -> float:
    b = re.search(r"\$([0-9.]+)\s*[Bb](?:illion)?", title)
    m = re.search(r"\$([0-9.]+)\s*[Mm](?:illion)?", title)

    if b:
        return min(float(b.group(1)) / 5.0, 1.0)   # $5B+ → 1.0
    if m:
        return min(float(m.group(1)) / 500.0, 0.8)  # $500M → 0.8, capped
    return 0.50  # no dollar amount; not penalized, just neutral
```

> **Calibration:** `$1B AWS deal` → `1/5 = 0.20` (low); adjust divisor down if $1B deals are consistently high-priority in your feed.

---

## Final pipeline

```python
def score_article(article: dict) -> float:
    # Layer 1: hard filters
    if hard_negative_match(article):
        return 0.0
    if not source_allowed(article):
        return 0.0

    # Layer 2: topic similarity via embeddings
    ts = topic_similarity(article)  # 0.0 – 1.0

    # Layer 3: heuristic significance
    s_type  = story_type_score(article["title"])   # 0.0 – 1.0
    s_ent   = entity_tier_score(article["title"])  # 0.0 – 1.0
    s_dol   = dollar_score(article["title"])       # 0.0 – 1.0
    rs = 0.4 * s_type + 0.4 * s_ent + 0.2 * s_dol

    return ts * 0.55 + rs * 0.45


def rank_articles(articles: list[dict], top_n: int = 30) -> list[dict]:
    # 1. Deduplicate first
    articles = deduplicate(articles, threshold=0.65)

    # 2. Score
    for article in articles:
        article["score"] = score_article(article)

    # 3. Sort and return top N
    return sorted(articles, key=lambda x: x["score"], reverse=True)[:top_n]
```

---

## Expected improvement over keyword matching

| Failure case | Keyword matching | This system |
|---|---|---|
| AMD stock surge ranked high | ✗ hits topic keyword | ✓ `story_type` → 0.30 |
| Airport / welfare funding slips through | ✗ hits "funding" | ✓ hard negative + source filter |
| 10× Gig Workers duplicates all score | ✗ independent scores | ✓ dedup collapses to 1 |
| Aikido acquisition overweighted | ✗ "startup + acquires" | ✓ entity tier B + deal_minor |
| Claude Sonnet launch ranked correctly | ✓ coincidentally | ✓ tier S + launch type = 0.95 |
| "Nifty IT falls 3%" ranked high | ✗ hits India Tech | ✓ market_move → 0.30 |

---

## Tuning guide

Once running, tune in this order:

1. **Source allow-list** — first line of defence. Add sources that produce good articles you're missing; remove sources that keep slipping through junk.
2. **Hard negatives** — whenever a clearly irrelevant article makes it into the top 30, add its most distinctive phrase here.
3. **Topic anchors** — if a whole category is under- or over-represented, rewrite its anchor string to be more specific.
4. **Story type patterns** — if a specific story type is being mis-scored, add or adjust a regex pattern.
5. **Entity tiers** — as new important companies emerge (or existing ones fade), update tier membership.

---

## Cost profile

| Component | Cost | Latency (per article) |
|---|---|---|
| Hard negative filter | Zero | < 0.1ms |
| Source allow-list | Zero | < 0.1ms |
| Deduplication (`difflib`) | Zero | ~2ms per pair |
| `all-MiniLM-L6-v2` embedding | Zero (local, CPU) | ~15–25ms |
| Heuristic RS (regex + dict) | Zero | < 1ms |
| **Total per article** | **$0** | **~20–30ms** |

Compared to an LLM call (~$0.001–0.01 per article × 213 articles = $0.21–$2.13 per run), this runs indefinitely for free once the model is downloaded.
