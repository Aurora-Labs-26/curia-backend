"""
Local article ranking — no LLM call.

Runs as a pre-filter *before* the Score & Curate LLM call (see
pipeline.PipelineManager.run_rank_step): it doesn't try to produce a
precision-ranked final order, it only needs to be recall-safe enough that the
bottom half of the pool can be dropped without discarding anything actually
important, cutting LLM input tokens roughly in half.

Architecture (main pool):
  TS (topic similarity)  →  batch sentence-embedding cosine sim against each
                             active interest's topic sub-anchors (max across
                             sub-anchors, then max across interests)
  Clustering              →  greedy same-story clustering (cosine >
                             CLUSTER_SIM_THRESHOLD) collapses duplicate
                             coverage of one event into a single representative
                             (the highest-TS member); cluster size feeds RS
  RS (real-world          →  blend of cluster size (more outlets covering the
      significance)          same event → more significant) and title-level
                             heuristics (story type / dollar amount) — the
                             heuristic half catches single-sourced-but-
                             important stories that cluster size alone
                             would score at zero
  Composite               →  (TS × TS_WEIGHT + RS × RS_WEIGHT) × 10 + multi-topic boost

"Local:"-tagged articles get one thing from the main pipeline: dedup. They're
embedded and run through the same greedy same-story clustering pass (own
pass, own pool — never merged with main) so that multiple outlets
covering one local event collapse into a single representative entry instead
of appearing as separate items. Beyond that they're deliberately NOT scored
like main articles: TS stays a fixed neutral value (no topic anchors, no
interest-gating — there's no LOCAL_TOPIC_ANCHORS), and they are never subject
to the keep_fraction cut — every deduped local entry is always included.

No hard source-allowlist or hard-negative-phrase gate: both were exclusionary
(drop entirely if unlisted/matched), which risks silently killing a real
article on a title collision. Everything here only produces gradations.

No named-entity tier list either (an earlier version scored titles against a
fixed S/A/B tier of hardcoded company names — OpenAI/NVIDIA/Apple as "S",
regional names like Zomato/Swiggy as "B"). That baked in global-press-volume
bias as if it were significance, permanently under-scoring any market/company
not on the list (and never adapting to the user's own location/interests).
Same category of mistake as hardcoding entities into TOPIC_ANCHORS — removed
for the same reason.
"""
import asyncio
import re
from typing import List, Dict, Any, Optional

from brief import enrichment as enrichment_service

from loguru import logger

# ---------------------------------------------------------------------------
# TOPIC ANCHORS — focused sub-anchor strings per Beat (must match the Beat names
# in news_service.BEATS / the dashboard's interest chips — NOT the legacy
# pre-Beats IAB category names). Per-topic score = max cosine across its
# sub-anchors, so every topic is built from a deliberately even set of distinct
# angles — no sub-angle gets extra anchors over its siblings — but the Beats
# that genuinely span more ground get more anchors overall (Science & Health,
# Culture, Lifestyle — each merges several of the old narrow IAB categories).
#
# Anchors are written as concrete, headline-like scenarios (concrete verbs:
# "fined", "raises funding", "launches", "sets new benchmark records") rather
# than abstract encyclopedia-style category descriptions — verified this
# roughly doubles cosine similarity against real headlines (e.g. an abstract
# antitrust anchor scored 0.26 against a real antitrust headline, a concrete
# scenario version scored 0.36+) because MiniLM cosine tracks phrasing/verb
# structure more than pure topic membership.
#
# Deliberately NO named companies/products/people in any anchor. An earlier
# version named specific entities (OpenAI, Anthropic, Google...) to chase
# that same concreteness boost, but that's keyword-matching in disguise: it
# measurably failed to generalize (a "Claude Sonnet 5" headline scored LOWER
# against an anchor naming Anthropic than against a generic "an AI lab
# releasing a new model" anchor) and would silently degrade for any entity
# not on the list. Concrete scenario + generic role-noun ("a major tech
# company", "an AI lab", "a chipmaker") gets most of the concreteness benefit
# without hardcoding entities that go stale or don't generalize.
#
# ...so every topic is built from a deliberately even set of distinct,
# non-overlapping angles — no sub-angle gets extra anchors over its siblings
# — but the Beats that genuinely span more ground get more anchors overall
# (Science & Health, Culture, Lifestyle, and Tech — each either merges
# several of the old narrow IAB categories, or, for Tech, has to cover the
# full breadth of GNews' general TECHNOLOGY vertical rather than just
# AI/semiconductor news; see news_service.BEATS). Business's and Sports'
# anchor counts grew too, but that's correcting earlier under-coverage —
# Sports in particular had only 4 anchors for a sport with many distinct
# story types — not because those two Beats are unusually broad.
# ---------------------------------------------------------------------------
TOPIC_ANCHORS: Dict[str, List[str]] = {
    # AI and semiconductors are real, high-volume angles, but must never
    # again be a majority of this list (an earlier version had 4 of 8
    # anchors on just those two sub-verticals) — the rest of Tech's anchors
    # exist to cover the other angles GNews' general TECHNOLOGY vertical
    # actually surfaces: consumer devices, software/platforms, cloud infra,
    # robotics/AV, cybersecurity, crypto, and telecom.
    "Tech": [
        "An AI lab releasing a new model that sets new performance or benchmark records",
        "A company building or deploying AI agents, coding assistants, chatbots, or automated AI tools for other businesses",
        "A chipmaker launching a new processor or GPU, or expanding semiconductor manufacturing capacity",
        "Export controls, sanctions, or supply chain restrictions affecting the semiconductor industry",
        "A major technology company reporting quarterly earnings or announcing a strategic corporate shift",
        "A major tech company fined, sued, or investigated by regulators for anticompetitive behavior",
        "A company suffering a data breach, ransomware attack, or cybersecurity incident that exposes user data",
        "One technology company acquiring or merging with another in a large corporate deal",
        "A company unveiling a new smartphone, laptop, wearable device, or other consumer gadget",
        "A company rolling out a major redesign, new feature, or update to a widely used app or social media platform",
        "A company launching a new cloud computing service, data center, or enterprise software platform",
        "A company unveiling a new robot, autonomous vehicle, or industrial automation system",
        "A cryptocurrency exchange or blockchain platform launching a new product, being hacked, or facing regulatory action",
        "A telecom carrier launching a new wireless network, satellite internet service, or connectivity technology",
    ],
    "Business": [
        "A central bank raising or cutting interest rates, or new data on inflation or economic growth",
        "Two companies merging, one acquiring another, or a company reporting quarterly earnings",
        "New tariffs, a trade dispute, or sanctions disrupting global supply chains and commerce",
        "The stock market moving sharply, commodity prices shifting, or a currency losing or gaining value",
        "A new jobs report, a wave of layoffs, or a shift in hiring and unemployment trends",
        "An early-stage company raising a new round of venture capital funding",
        "A financial regulator investigating or charging a company with fraud or securities violations",
        "A company going public in a new stock market listing, or reaching a new valuation milestone",
        "A company's CEO or top executive resigning, being replaced, or being newly appointed",
        "A company filing for bankruptcy, defaulting on debt, or undergoing a financial restructuring",
        "Workers going on strike, unionizing, or reaching a new labor agreement with an employer",
        "Oil, gas, or electricity prices shifting, or an energy company announcing a major production decision",
    ],
    "World": [
        "A war, military conflict, ceasefire, or escalation between countries or armed groups",
        "Diplomatic talks, a shift in foreign policy, or new sanctions between world powers",
        "A national election, referendum, or change in a country's government leadership",
        "A government passing a new law, policy, or executive action",
        "A court issuing a ruling on a major legal or constitutional question",
        "Countries negotiating a trade deal, treaty, or a decision by an international governing body",
        "An earthquake, flood, wildfire, or other natural disaster triggering a humanitarian relief effort",
        "Mass protests, civil unrest, or an attempted coup challenging a government",
        "A terrorist attack, security threat, or foiled plot prompting a government response",
        "A government official resigning or facing charges in a corruption or bribery scandal",
    ],
    "Science & Health": [
        "A space agency or private company launching a rocket, satellite, or space mission",
        "Researchers publishing a new scientific discovery or breakthrough finding",
        "Astronomers observing a new celestial object or cosmic phenomenon through a telescope",
        "New data or research on climate change and its effects on the environment",
        "A research institution receiving new funding for a major scientific study",
        "A disease outbreak or new virus spreading in a community or region",
        "Doctors or researchers publishing new medical study or clinical trial results",
        "A new diet, workout routine, or fitness trend becoming popular",
        "A hospital, health insurer, or healthcare company making a major business decision",
        "A regulator approving, rejecting, or recalling a new drug, vaccine, or medical device",
        "A new study, treatment, or awareness campaign focused on mental health or emotional wellbeing",
    ],
    "Culture": [
        "A new movie premiering in theaters, or new box office numbers being reported",
        "A TV show premiering, getting renewed, or being cancelled",
        "A musician releasing a new album or single, or announcing a concert tour",
        "A celebrity scandal, an award show, or a major pop culture moment",
        "A new book being released, or an author winning a major literary award",
        "A museum or gallery opening a new art exhibition, or artwork selling at auction",
        "A designer unveiling a new collection at a major fashion show",
        "A studio or streaming service announcing a new content strategy",
        "A new video game launching, or a major esports tournament taking place",
        "A social media trend, meme, or online creator going viral",
    ],
    "Lifestyle": [
        "Advice on parenting, dating, marriage, or family and relationship dynamics",
        "A new restaurant opening, a trending recipe, or a food safety investigation",
        "A home renovation project, gardening tip, or interior design trend",
        "Home prices or mortgage interest rates rising or falling",
        "An airline announcing new routes, or a popular travel destination or vacation trend",
        "A major shopping holiday, retail discount, or shift in consumer spending habits",
        "A person picking up a new hobby, such as photography, painting, or crafting",
        "A person going hiking, camping, or another outdoor recreational activity",
        "Companies embracing remote work, flexible schedules, or a new workplace culture trend",
        "A university changing admissions requirements, or news on student loan debt",
        "A religious institution, spiritual practice, or cultural tradition making news",
        "Advice on budgeting, saving money, or managing personal finances",
        "A new beauty, skincare, or grooming trend or product becoming popular",
    ],
    "Sports": [
        "A team winning or losing a game, match, or championship",
        "An athlete signing a new contract, being traded, or transferring to a different team",
        "A sports league signing a new broadcasting deal, or a team changing ownership",
        "A major international sports tournament or championship taking place",
        "An individual athlete setting a new record or achieving a career milestone in their sport",
        "An athlete or team sanctioned for doping, cheating, or another rule violation",
        "A team hiring or firing its head coach or manager",
        "An athlete announcing retirement, or returning to competition after an injury",
        "An athlete or team signing a new sponsorship or endorsement deal",
        "A sportsbook, league, or state announcing a new sports betting partnership or regulation",
    ],
}

# ---------------------------------------------------------------------------
# COMPOSITE WEIGHTS — how much TS vs RS counts toward the final 0-10 score.
# Single shared weight pair — local articles don't get their own topic
# anchors or their own weighting (see module docstring: local only gets
# dedup). Referenced from enrichment_service.py too — don't reintroduce
# these as separate literals there.
# ---------------------------------------------------------------------------
TS_WEIGHT = 0.55
RS_WEIGHT = 0.45

# ---------------------------------------------------------------------------
# CLUSTER-BASED SIGNIFICANCE (half of RS)
# Multiple outlets covering the same event = that event is important.
#   cluster of 1  → cluster_RS = 0.0  (unique / unverified)
#   cluster of 3  → cluster_RS = 0.4
#   cluster of 6+ → cluster_RS = 1.0  (saturated)
# ---------------------------------------------------------------------------
CLUSTER_SIM_THRESHOLD = 0.72   # cosine sim above which two articles share a story
CLUSTER_SATURATION    = 5      # cluster size at which cluster_RS maxes out

# Cap on how many other-outlet URLs to remember per cluster as same-story
# fallback candidates (see `cluster_alternates` below) — used later by Step 3b
# (enrichment_service.fetch_full_texts) if the picked representative's own
# page can't be scraped. A story rarely needs more than a few alternates tried.
MAX_ALTERNATES_PER_ARTICLE = 5

# ---------------------------------------------------------------------------
# HEURISTIC SIGNIFICANCE (other half of RS) — title-level regex/dict scoring.
# Catches significant single-sourced stories that cluster size alone would
# score at zero (e.g. a wire-service exclusive nobody else has picked up yet).
# ---------------------------------------------------------------------------
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


def dollar_score(title: str) -> float:
    b = re.search(r"\$([0-9.]+)\s*[Bb](?:illion)?", title)
    m = re.search(r"\$([0-9.]+)\s*[Mm](?:illion)?", title)
    if b:
        return min(float(b.group(1)) / 5.0, 1.0)   # $5B+ → 1.0
    if m:
        return min(float(m.group(1)) / 500.0, 0.8)  # $500M → 0.8, capped
    return 0.50  # no dollar amount; neutral, not penalized


def heuristic_rs(title: str) -> float:
    return (
        0.65 * story_type_score(title)
        + 0.35 * dollar_score(title)
    )


# ---------------------------------------------------------------------------
# MULTI-TOPIC INTERSECTION BONUS
# Each active topic whose raw cosine exceeds MULTI_TOPIC_THRESHOLD adds
# BOOST_PER_TOPIC to the composite (0–10 scale), capped at MAX_BOOST.
# ---------------------------------------------------------------------------
MULTI_TOPIC_THRESHOLD = 0.40
BOOST_PER_TOPIC      = 0.30
MAX_BOOST            = 0.60

# Fraction of the non-local pool passed through to the LLM (by composite rank).
# "Local:"-tagged articles are always kept regardless of this cutoff.
DEFAULT_KEEP_FRACTION = 0.5

# ---------------------------------------------------------------------------
# MODEL SINGLETON
# ---------------------------------------------------------------------------
_model = None
_topic_embeddings: Optional[Dict[str, Any]] = None   # {topic: (n_subs, 384)}


def _get_model():
    global _model, _topic_embeddings
    if _model is not None:
        return _model
    import torch
    from sentence_transformers import SentenceTransformer
    # Belt-and-suspenders alongside the OMP/MKL/OPENBLAS env vars set in
    # app/main.py (those must be set before torch is ever imported anywhere
    # in the process to reliably take effect; this covers torch's own
    # intra-op thread pool directly, in case something already imported it
    # first). See app/main.py's top-of-file comment for the full explanation
    # — CPU-quota-limited containers thrash badly when torch spawns more
    # threads than the container is actually entitled to run concurrently.
    torch.set_num_threads(1)
    logger.info("Loading all-MiniLM-L6-v2 ...")
    _model = SentenceTransformer("all-MiniLM-L6-v2")
    _topic_embeddings = {
        topic: _model.encode(subs)
        for topic, subs in TOPIC_ANCHORS.items()
    }
    logger.info(
        "Model loaded — %d topics (%d sub-anchors total).",
        len(_topic_embeddings),
        sum(len(v) for v in _topic_embeddings.values()),
    )
    return _model


async def warm_model() -> None:
    """Eagerly loads the model + topic anchor embeddings at app startup (see
    app/main.py) instead of lazily on the first request. _get_model() does 7
    model.encode() calls (one per topic anchor) which, even after the thread-
    count fix above, is still a one-time cost worth paying at boot rather
    than inside a user's first Pre-Opt request — every second here is a
    second not spent inside that request's gateway-timeout budget, for
    embeddings that never change across the process's lifetime anyway."""
    await asyncio.to_thread(_get_model)


def _recluster_after_enrichment(ranked: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Second, lightweight clustering pass over the already-deduped non-local
    entries, run only after enrichment has replaced some entries' description
    with real scraped article text. The first clustering pass (in
    rank_articles) runs on title+weak-RSS-description, which sometimes misses
    real duplicates — two outlets covering the same event but phrasing the
    headline differently enough to land under CLUSTER_SIM_THRESHOLD. Richer
    text usually pushes genuine duplicates over that threshold (measured
    example: two outlets' unicorn-funding stories went from 0.55-0.72 cosine
    on titles alone to 0.75-0.90 using scraped lead paragraphs), so it's worth
    re-checking once better text exists. Never runs when enrichment is
    disabled — re-clustering unchanged text would just reproduce the same
    clusters for no benefit. Local entries never go through enrichment, so
    they're passed through untouched here too.
    """
    from sklearn.metrics.pairwise import cosine_similarity

    non_local_idx = [i for i, e in enumerate(ranked) if not e["is_local"]]
    if len(non_local_idx) < 2:
        return ranked

    model = _get_model()
    texts = [f"{ranked[i]['title']} {ranked[i]['description']}".strip() for i in non_local_idx]
    embeddings = model.encode(texts)
    sim_matrix = cosine_similarity(embeddings)

    assigned = [False] * len(non_local_idx)
    survivors: List[Dict[str, Any]] = []

    for seed in range(len(non_local_idx)):
        if assigned[seed]:
            continue
        members = [
            j for j in range(len(non_local_idx))
            if not assigned[j] and sim_matrix[seed, j] > CLUSTER_SIM_THRESHOLD
        ]
        for j in members:
            assigned[j] = True

        if len(members) == 1:
            survivors.append(ranked[non_local_idx[members[0]]])
            continue

        entries = [ranked[non_local_idx[j]] for j in members]
        best = max(entries, key=lambda e: e["composite"])
        combined_size = sum(e["cluster_size"] for e in entries)
        cluster_rs = min(combined_size - 1, CLUSTER_SATURATION) / CLUSTER_SATURATION
        h_rs = heuristic_rs(best["title"])
        rs = 0.5 * cluster_rs + 0.5 * h_rs
        ts = best["scores"]["topicSimilarity"] / 10.0
        boost = best["scores"].get("multiTopicBoost", 0.0)

        best["cluster_size"] = combined_size
        best["scores"]["clusterSignificance"] = round(cluster_rs * 10, 1)
        best["scores"]["heuristicSignificance"] = round(h_rs * 10, 1)
        best["scores"]["significance"] = round(rs * 10, 1)
        best["composite"] = round((ts * TS_WEIGHT + rs * RS_WEIGHT) * 10 + boost, 2)
        best["re_clustered"] = True

        logger.debug(
            "POST-ENRICHMENT MERGE | combined_size=%d kept=%s dropped=%s",
            combined_size, best["title"][:70],
            [e["title"][:70] for e in entries if e is not best],
        )
        survivors.append(best)

    return [ranked[i] for i in range(len(ranked)) if ranked[i]["is_local"]] + survivors


async def rank_articles(
    articles: List[Dict[str, Any]],
    interests: List[str],
    keep_fraction: float = DEFAULT_KEEP_FRACTION,
) -> Dict[str, Any]:
    """
    Score, cluster-dedup, and rank the full raw article pool with no LLM call.

    Returns every article (as one entry per event cluster — including
    "Local:" articles, which are deduped the same way but never scored or
    cut, see module docstring) sorted by composite score, each annotated
    with its score breakdown and an `included` flag marking whether it's in
    the top `keep_fraction` of the non-local pool — that subset is what
    should be forwarded to the Score & Curate LLM step.

    Optionally refines scores using real scraped article text before the cut
    is computed — see enrichment_service.ENRICHMENT_ENABLED to turn that off.
    """
    from sklearn.metrics.pairwise import cosine_similarity

    model = _get_model()

    main_articles:  List[Dict[str, Any]] = []
    local_articles: List[Dict[str, Any]] = []
    for art in articles:
        if art.get("topic", "").startswith("Local:"):
            local_articles.append(art)
        else:
            main_articles.append(art)

    def _entry(art, ts, cluster_rs, cluster_size, is_local):
        h_rs = heuristic_rs(art.get("title", ""))
        rs = 0.5 * cluster_rs + 0.5 * h_rs
        composite = round((ts * TS_WEIGHT + rs * RS_WEIGHT) * 10, 2)
        return {
            "title": art.get("title", ""),
            "description": art.get("description", ""),
            "source": art.get("source", ""),
            "url": art.get("url", ""),
            "topic": art.get("topic", ""),
            "published_date": art.get("published_date", ""),
            "is_local": is_local,
            "cluster_size": cluster_size,
            "scores": {
                "topicSimilarity": round(ts * 10, 1),
                "clusterSignificance": round(cluster_rs * 10, 1),
                "heuristicSignificance": round(h_rs * 10, 1),
                "significance": round(rs * 10, 1),
            },
            "composite": composite,
        }

    ranked: List[Dict[str, Any]] = []
    # index into `ranked` -> URLs of every article folded into that cluster,
    # used only by the optional enrichment step below (cluster-borrowing).
    cluster_member_urls: Dict[int, List[str]] = {}

    def _embed_cluster_score(pool, is_local, topic_scorer):
        """Shared embed -> per-article TS -> greedy same-story clustering
        pass, used for both pools (called once each below) — they never
        cluster against each other, only within their own pool.
        `topic_scorer(emb) -> (ts, boost)` is the one piece that differs:
        interest-gated multi-topic Beat scoring for main, a fixed neutral
        (0.5, 0.0) for local — local articles are deduped by this same
        clustering pass but never actually topic-scored (see module
        docstring)."""
        if not pool:
            return

        texts = [
            f"{art.get('title', '')} {art.get('description', '')}".strip()
            for art in pool
        ]
        embeddings = model.encode(texts)

        ts_list: List[float] = []
        boost_list: List[float] = []
        for emb in embeddings:
            ts, boost = topic_scorer(emb)
            ts_list.append(ts)
            boost_list.append(boost)

        # Greedy clustering: pull in every unassigned neighbour above threshold
        # in a single pass per seed; keep the highest-TS member as representative.
        sim_matrix = cosine_similarity(embeddings)
        assigned = [False] * len(pool)

        for seed in range(len(pool)):
            if assigned[seed]:
                continue
            members = [
                j for j in range(len(pool))
                if not assigned[j] and sim_matrix[seed, j] > CLUSTER_SIM_THRESHOLD
            ]
            for j in members:
                assigned[j] = True

            cluster_size = len(members)
            cluster_rs = min(cluster_size - 1, CLUSTER_SATURATION) / CLUSTER_SATURATION
            rep = max(members, key=lambda j: ts_list[j])

            entry = _entry(
                pool[rep], ts=ts_list[rep], cluster_rs=cluster_rs,
                cluster_size=cluster_size, is_local=is_local,
            )
            if boost_list[rep]:
                entry["composite"] = round(entry["composite"] + boost_list[rep], 2)
                entry["scores"]["multiTopicBoost"] = round(boost_list[rep], 2)
            # Same-story alternates: other outlets folded into this cluster,
            # kept as fallback candidates for Step 3b if the representative's
            # own page can't be scraped later. Travels with the entry itself
            # (not a side dict keyed by index) so it survives reordering.
            entry["cluster_alternates"] = [
                {"url": pool[j]["url"], "source": pool[j].get("source", "")}
                for j in members if j != rep and pool[j].get("url")
            ][:MAX_ALTERNATES_PER_ARTICLE]
            cluster_member_urls[len(ranked)] = [
                pool[j]["url"] for j in members if pool[j].get("url")
            ]
            ranked.append(entry)

            if cluster_size > 1:
                dropped = [pool[j]["title"][:70] for j in members if j != rep]
                logger.debug(
                    "CLUSTER | local=%s size=%d kept=%s dropped=%s",
                    is_local, cluster_size, entry["title"][:70], dropped,
                )

    def _main_topic_scorer(emb):
        topic_scores = [
            float(cosine_similarity([emb], anchor_emb)[0].max())
            for topic_name, anchor_emb in (_topic_embeddings or {}).items()
            if topic_name in interests
        ]
        ts = max(topic_scores) if topic_scores else 0.5
        hits = sum(1 for s in topic_scores if s > MULTI_TOPIC_THRESHOLD)
        boost = min((hits - 1) * BOOST_PER_TOPIC, MAX_BOOST) if hits > 1 else 0.0
        return ts, boost

    def _local_topic_scorer(_emb):
        # No topic scoring for local — fixed neutral TS, no boost. This pass
        # is only run to get real embeddings for the clustering/dedup step.
        return 0.5, 0.0

    # _embed_cluster_score is synchronous/CPU-bound (model.encode() is a real
    # blocking call, not real async I/O) — offloaded to a worker thread so it
    # doesn't freeze the event loop and serialize every other concurrently-
    # running rank_articles() call (e.g. Pre-Opt's 7 topics).
    await asyncio.to_thread(_embed_cluster_score, main_articles, False, _main_topic_scorer)
    await asyncio.to_thread(_embed_cluster_score, local_articles, True, _local_topic_scorer)

    # Optional accuracy refinement — see enrichment_service.py for the on/off
    # switch and full explanation. Runs BEFORE the cut below so a better score
    # can actually change who makes it in, not just reorder who already did.
    # Any failure (disabled, missing deps, network outage, timeout) leaves
    # `ranked` exactly as computed above — this call is not required for a
    # valid result. Local entries are skipped inside enrich_and_rescore (they
    # have no topic anchors to refine against).
    if enrichment_service.ENRICHMENT_ENABLED:
        try:
            await enrichment_service.enrich_and_rescore(
                ranked, cluster_member_urls, interests, _topic_embeddings or {}, model,
            )
            # _recluster_after_enrichment is synchronous/CPU-bound (another
            # direct model.encode() call) — offloaded to a worker thread for
            # the same reason as the _embed_cluster_score calls above.
            ranked = await asyncio.to_thread(_recluster_after_enrichment, ranked)
        except Exception as e:
            logger.warning(f"Article enrichment step failed, continuing without it: {e}")

    # Local: always included after dedup, never subject to the fraction cut.
    local = [e for e in ranked if e["is_local"]]
    for entry in local:
        entry["included"] = True

    # Non-local (main) pool: cut at the top `keep_fraction` by composite rank —
    # EXCEPT "Custom:"-tagged entries (user-typed Custom Topics, see
    # news_service.fetch_articles_for_profile), which stay in the main pool for
    # ranking/editorial purposes (they still compete for the global slots, not
    # the Local Pulse one) but are always force-included, same as Local:
    # articles above. They have no TOPIC_ANCHORS to score fairly against, so
    # cutting them here would silently defeat the reason they were fetched.
    non_local = [e for e in ranked if not e["is_local"]]
    non_local.sort(key=lambda e: e["composite"], reverse=True)
    keep_count = max(1, round(len(non_local) * keep_fraction)) if non_local else 0
    for i, entry in enumerate(non_local):
        entry["rank"] = i + 1
        entry["included"] = i < keep_count or entry["topic"].startswith("Custom:")

    ranked = local + non_local
    ranked.sort(key=lambda e: e["composite"], reverse=True)

    kept_total = sum(1 for e in ranked if e["included"])
    logger.info(
        "rank_articles: %d raw -> %d clusters (%d local after dedup / %d main) -> "
        "%d kept (%.0f%% of non-local pool)",
        len(articles), len(ranked), len(local), len(non_local), kept_total, keep_fraction * 100,
    )

    return {
        "ranked": ranked,
        "total_count": len(ranked),
        "kept_count": kept_total,
        "keep_fraction": keep_fraction,
    }
