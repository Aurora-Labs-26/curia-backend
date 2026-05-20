"""
intelligence/idea_generator.py
LangGraph workflow — generates show ideas from the archive.
Runs in background, writes to show_ideas table.

Workflow:
  load_archive → cluster_sources → diff_clusters → evaluate_ideas → filter_covered → save_ideas → auto_generate
"""

import json
import os
from typing import TypedDict

from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from loguru import logger

from core.db.connection import db_execute, db_fetchrow, db_query
from core.embeddings import get_embedding
from core.kb import UserKB, load_kb
from core.prompts.idea_evaluation import (
    evaluate_ideas_batch,
    evaluate_single_idea,
)

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../.env"))

SIMILARITY_THRESHOLD = 0.70   # min cosine score on primitive embeddings to form a clique
MAX_CLUSTER_SIZE = 5          # cap cluster size to keep episodes focused


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class IdeaGenState(TypedDict, total=False):
    user_id: str
    user_kb: UserKB | None        # KB loaded once at archive load; propagated to evaluator
    sources: list[dict]           # [{id, title, insights: {type: content}}]
    clusters: list[list[str]]     # groups of source_ids
    raw_ideas: list[dict]         # ideas from evaluate_ideas
    filtered_ideas: list[dict]    # ideas passed to save
    saved_count: int
    auto_generated_count: int


# ---------------------------------------------------------------------------
# Node: load_archive
# ---------------------------------------------------------------------------

async def load_archive(state: IdeaGenState) -> IdeaGenState:
    user_id = state["user_id"]
    logger.info(f"[load_archive] Loading archive for user {user_id}")

    # Fetch all sources for this user
    sources_raw = await db_query(
        "SELECT id, title FROM source WHERE user_id = $user_id ORDER BY created_at DESC",
        {"user_id": user_id},
    )

    # Fetch all insights for this user's sources
    insights_raw = await db_query(
        """
        SELECT si.source_id, si.insight_type, si.content
        FROM source_insight si
        JOIN source s ON s.id = si.source_id
        WHERE s.user_id = $user_id
        """,
        {"user_id": user_id},
    )

    # Build insights map keyed by bare ID (no "source:" prefix)
    insights_map: dict[str, dict] = {}
    for row in (insights_raw or []):
        sid = str(row["source_id"]).replace("source:", "")
        if sid not in insights_map:
            insights_map[sid] = {}
        insights_map[sid][row["insight_type"]] = row["content"]

    # Assemble sources with insights — look up by bare ID
    sources = []
    for s in (sources_raw or []):
        sid = str(s["id"])
        bare_sid = sid.replace("source:", "")
        sources.append({
            "id": sid,
            "title": s.get("title", "Untitled"),
            "insights": insights_map.get(bare_sid, {}),
        })

    logger.info(f"[load_archive] {len(sources)} sources loaded")

    # Load the user's KB once; downstream nodes use it for filtering + prompt enrichment.
    try:
        user_kb = await load_kb(user_id)
    except Exception as e:
        logger.warning(f"[load_archive] could not load KB for {user_id}: {e}; proceeding without")
        user_kb = None

    return {**state, "sources": sources, "user_kb": user_kb}


# ---------------------------------------------------------------------------
# Node: cluster_sources
# ---------------------------------------------------------------------------

async def cluster_sources(state: IdeaGenState) -> IdeaGenState:
    sources = state["sources"]
    logger.info(f"[cluster_sources] Clustering {len(sources)} sources")

    if len(sources) < 2:
        # Nothing to cluster — treat each as standalone
        clusters = [[s["id"]] for s in sources]
        return {**state, "clusters": clusters}

    # Fetch primitive embeddings (core_tensions + counterpoints) per source
    from core.embeddings import get_embedding_column
    emb_col = get_embedding_column()
    source_embeddings: dict[str, list[float]] = {}
    bare_ids = [str(source["id"]).replace("source:", "") for source in sources]
    sid_to_original = {str(source["id"]).replace("source:", ""): str(source["id"]) for source in sources}
    rows = await db_query(
        f"SELECT source_id, {emb_col} FROM source_primitive_embedding WHERE source_id = ANY($ids::uuid[])",
        {"ids": bare_ids},
    )
    for row in (rows or []):
        emb = row.get(emb_col)
        if emb is not None:
            bare = str(row["source_id"])
            orig_sid = sid_to_original.get(bare, bare)
            source_embeddings[orig_sid] = list(emb) if hasattr(emb, "__iter__") else emb

    no_embedding = [s["id"] for s in sources if s["id"] not in source_embeddings]
    logger.info(f"[cluster_sources] Got primitive embeddings for {len(source_embeddings)} sources")
    if no_embedding:
        id_to_title = {s["id"]: s.get("title", "?") for s in sources}
        for sid in no_embedding:
            logger.debug(f"[cluster_sources] NO_EMBEDDING: {id_to_title.get(sid, sid)[:80]}")

    # Cosine similarity
    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = sum(x ** 2 for x in a) ** 0.5
        mag_b = sum(x ** 2 for x in b) ** 0.5
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    ids = list(source_embeddings.keys())

    # Load cached scores from DB
    bare_ids_for_cache = [sid.replace("source:", "") for sid in ids]
    cached_rows = await db_query(
        """
        SELECT source_a::text, source_b::text, score
        FROM source_similarity
        WHERE source_a = ANY($ids::uuid[]) AND source_b = ANY($ids::uuid[])
        """,
        {"ids": bare_ids_for_cache},
    )
    scores = {}
    for row in (cached_rows or []):
        a = str(row["source_a"])
        b = str(row["source_b"])
        # map bare uuid back to original sid key
        a_key = sid_to_original.get(a, a)
        b_key = sid_to_original.get(b, b)
        scores[(a_key, b_key)] = row["score"]
        scores[(b_key, a_key)] = row["score"]

    # Compute missing pairs and cache them
    new_scores: list[dict] = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if (ids[i], ids[j]) not in scores:
                s = cosine(source_embeddings[ids[i]], source_embeddings[ids[j]])
                scores[(ids[i], ids[j])] = s
                scores[(ids[j], ids[i])] = s
                bare_i = ids[i].replace("source:", "")
                bare_j = ids[j].replace("source:", "")
                new_scores.append({"a": bare_i, "b": bare_j, "score": s})

    # Bulk-insert new scores
    for ns in new_scores:
        try:
            await db_execute(
                """
                INSERT INTO source_similarity (source_a, source_b, score)
                VALUES ($a::uuid, $b::uuid, $score)
                ON CONFLICT DO NOTHING
                """,
                {"a": ns["a"], "b": ns["b"], "score": ns["score"]},
            )
        except Exception as e:
            logger.warning(f"[cluster_sources] cache write failed: {e}")

    logger.info(f"[cluster_sources] {len(new_scores)} new pairs computed, {len(scores)//2 - len(new_scores)} from cache")

    # Log all above-threshold pairs so you can see what's connecting
    id_to_title = {s["id"]: s.get("title", "?") for s in sources}
    above = sorted(
        [(a, b, sc) for (a, b), sc in scores.items() if a < b and sc >= SIMILARITY_THRESHOLD],
        key=lambda x: -x[2],
    )
    if above:
        logger.debug(f"[cluster_sources] {len(above)} pairs above threshold ({SIMILARITY_THRESHOLD}):")
        for a, b, sc in above:
            logger.debug(f"  {sc:.3f}  '{id_to_title.get(a, a)[:50]}'  ↔  '{id_to_title.get(b, b)[:50]}'")
    else:
        logger.debug(f"[cluster_sources] No pairs above threshold ({SIMILARITY_THRESHOLD}) — all sources will be standalone")

    # Log below-threshold pairs at trace level (high volume — only useful for deep debugging)
    below = sorted(
        [(a, b, sc) for (a, b), sc in scores.items() if a < b and sc < SIMILARITY_THRESHOLD],
        key=lambda x: -x[2],
    )
    for a, b, sc in below:
        logger.trace(f"  {sc:.3f}  BELOW '{id_to_title.get(a, a)[:50]}'  ↔  '{id_to_title.get(b, b)[:50]}'")

    def is_clique(members):
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                if scores.get((members[i], members[j]), scores.get((members[j], members[i]), 0.0)) < SIMILARITY_THRESHOLD:
                    return False
        return True

    # Build maximal cliques — start from each qualifying pair, expand greedily
    raw_cliques = []
    pairs = [(a, b) for (a, b), sc in scores.items() if a < b and sc >= SIMILARITY_THRESHOLD]
    for a, b in pairs:
        clique = [a, b]
        for sid in ids:
            if sid in clique or len(clique) >= MAX_CLUSTER_SIZE:
                continue
            if is_clique(clique + [sid]):
                clique.append(sid)
        raw_cliques.append(tuple(sorted(clique)))

    # Deduplicate and keep only maximal cliques (remove subsets)
    raw_cliques = list(set(raw_cliques))
    raw_cliques.sort(key=lambda c: -len(c))
    maximal = []
    for c in raw_cliques:
        if not any(set(c).issubset(set(m)) and c != m for m in maximal):
            maximal.append(c)

    clusters = [list(c) for c in maximal]

    # Sources with no primitive embedding or not in any clique — standalone
    in_clique = set(sid for c in maximal for sid in c)
    for source in sources:
        sid = source["id"]
        if sid not in in_clique:
            clusters.append([sid])

    # Log final cluster membership
    for i, cluster in enumerate(clusters):
        members = ", ".join(f"'{id_to_title.get(sid, sid)[:40]}'" for sid in cluster)
        kind = "cluster" if len(cluster) > 1 else "standalone"
        logger.info(f"[cluster_sources] [{kind}] {members}")

    logger.info(f"[cluster_sources] {len(clusters)} clusters formed ({len([c for c in clusters if len(c) > 1])} multi-source, {len([c for c in clusters if len(c) == 1])} standalone)")
    return {**state, "clusters": clusters}


# ---------------------------------------------------------------------------
# Node: diff_clusters
# ---------------------------------------------------------------------------

async def diff_clusters(state: IdeaGenState) -> IdeaGenState:
    user_id = state["user_id"]
    clusters = state["clusters"]

    existing = await db_query(
        "SELECT source_ids FROM show_idea WHERE user_id = $user_id",
        {"user_id": user_id}
    )
    existing_sets = [frozenset(str(s) for s in row["source_ids"]) for row in (existing or [])]

    new_clusters = []
    for cluster in clusters:
        cluster_set = frozenset(str(s) for s in cluster)
        if cluster_set not in existing_sets:
            new_clusters.append(cluster)

    logger.info(f"[diff_clusters] {len(clusters)} clusters → {len(new_clusters)} new after diff")
    return {**state, "clusters": new_clusters}


# ---------------------------------------------------------------------------
# Node: evaluate_ideas
# ---------------------------------------------------------------------------
#
# Prompts live in core/prompts/idea_evaluation.py as DSPy Signatures
# (EvaluateIdeasBatch + EvaluateSingleIdea). They are GEPA/MIPRO-compatible.


def has_complete_insights(source: dict) -> bool:
    """A source passes quality check if it has key_insights (tier 1, always present)."""
    insights = source.get("insights", {})
    return bool(insights.get("key_insights"))


def format_group(group_id: str, label: str, sources: list[dict]) -> str:
    lines = [f"--- GROUP_ID: {group_id} | {label} ---"]
    for s in sources:
        lines.append(f"Title: {s['title']}")
        insights = s.get("insights", {})
        if insights.get("key_insights"):
            lines.append(f"Key insights: {insights['key_insights']}")
        if insights.get("human_stakes"):
            lines.append(f"Human stakes: {insights['human_stakes']}")
        if insights.get("core_tensions"):
            lines.append(f"Core tensions: {insights['core_tensions']}")
        if insights.get("counterpoints"):
            lines.append(f"Counterpoints: {insights['counterpoints']}")
        if insights.get("examples"):
            lines.append(f"Examples: {insights['examples']}")
        lines.append("")
    return "\n".join(lines)


def parse_json_response(raw: str) -> list:
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def _format_user_context(kb: UserKB | None) -> str:
    """Render the bits of the KB the idea-eval LLM should consider when generating angles."""
    if kb is None:
        return ""
    lines: list[str] = []
    if kb.interests.topics:
        lines.append(f"Active interests: {', '.join(kb.interests.topics)}")
    if kb.interests.current_obsession:
        lines.append(f"Current obsession: {kb.interests.current_obsession}")
    if kb.preferences.preferred_formats:
        lines.append(f"Preferred formats: {', '.join(kb.preferences.preferred_formats)}")
    if kb.dislikes.themes:
        lines.append(f"Avoid themes: {', '.join(kb.dislikes.themes)}")
    if kb.dislikes.tones:
        lines.append(f"Avoid tones: {', '.join(kb.dislikes.tones)}")
    if not lines:
        return ""
    return (
        "USER CONTEXT (consider these preferences when generating angles):\n"
        + "\n".join(f"- {line}" for line in lines)
        + "\n\n"
    )


def _idea_violates_dislikes(angle: str, kb: UserKB | None) -> bool:
    """Crude post-filter — drop ideas whose angle text mentions a disliked theme."""
    if kb is None or not angle:
        return False
    angle_lower = angle.lower()
    for theme in kb.dislikes.themes or []:
        if theme and theme.lower() in angle_lower:
            return True
    return False


async def evaluate_ideas(state: IdeaGenState) -> IdeaGenState:
    clusters = state["clusters"]
    sources = state["sources"]
    user_kb = state.get("user_kb")
    source_map = {s["id"]: s for s in sources}

    logger.info(f"[evaluate_ideas] Building batch — {len(clusters)} clusters + standalones")

    # groups: dict keyed by group_id → (source_ids, idea_type, cluster_sources)
    groups = {}

    # Cluster groups
    for i, cluster in enumerate(clusters):
        cluster_sources = [source_map[sid] for sid in cluster if sid in source_map]
        if not cluster_sources:
            continue
        if not any(has_complete_insights(s) for s in cluster_sources):
            continue
        idea_type = "cluster" if len(cluster) > 1 else "standalone"
        group_id = f"g{i}"
        groups[group_id] = (cluster, idea_type, cluster_sources)

    if not groups:
        logger.info("[evaluate_ideas] No groups to evaluate")
        return {**state, "raw_ideas": []}

    # Build batch prompt with optional user-context header + group_ids embedded
    user_ctx = _format_user_context(user_kb)
    human = (
        f"{user_ctx}"
        f"Generate one episode idea per group below. Total groups: {len(groups)}\n\n"
    )
    for group_id, (_, idea_type, cluster_sources) in groups.items():
        label = idea_type.upper()
        human += format_group(group_id, label, cluster_sources) + "\n"

    logger.info(f"[evaluate_ideas] Sending batch of {len(groups)} groups to Haiku")

    raw_ideas = []
    try:
        # Batch call — one DSPy module invocation across all groups.
        prediction = evaluate_ideas_batch(groups_text=human)
        ideas = parse_json_response(prediction.ideas_json)

        for idea in ideas:
            group_id = idea.get("group_id")
            if not group_id or group_id not in groups:
                logger.warning(f"[evaluate_ideas] Unknown group_id in response: {group_id}")
                continue
            angle = idea.get("angle", "")
            if _idea_violates_dislikes(angle, user_kb):
                logger.info(f"[evaluate_ideas] DROP (dislikes match): {angle[:80]}")
                continue
            source_ids, idea_type, _ = groups[group_id]
            idea["source_ids"] = source_ids
            idea["type"] = idea_type
            raw_ideas.append(idea)
            logger.info(f"[evaluate_ideas] KEEP [{idea_type}]: {angle[:80]}")

    except Exception as e:
        logger.warning(f"[evaluate_ideas] Batch call failed: {e} — falling back to per-group calls")
        for group_id, (source_ids, idea_type, cluster_sources) in groups.items():
            try:
                # Per-group fallback — one DSPy module invocation per group.
                # Prepend the user context here too so each fallback call also gets it.
                prediction = evaluate_single_idea(
                    group_text=user_ctx + format_group(group_id, idea_type.upper(), cluster_sources)
                )
                idea = parse_json_response(prediction.idea_json)
                if isinstance(idea, list):
                    idea = idea[0]
                idea.pop("title", None)
                angle = idea.get("angle", "")
                if _idea_violates_dislikes(angle, user_kb):
                    logger.info(f"[evaluate_ideas] DROP fallback (dislikes match): {angle[:80]}")
                    continue
                idea["source_ids"] = source_ids
                idea["type"] = idea_type
                raw_ideas.append(idea)
                logger.info(f"[evaluate_ideas] KEEP (fallback) [{idea_type}]: {angle[:80]}")
            except Exception as e2:
                logger.warning(f"[evaluate_ideas] Fallback failed for {group_id}: {e2}")

    logger.info(f"[evaluate_ideas] {len(raw_ideas)} ideas generated")
    return {**state, "raw_ideas": raw_ideas}


async def filter_covered(state: IdeaGenState) -> IdeaGenState:
    # Freshness filter removed — user reviews ideas directly
    return {**state, "filtered_ideas": state["raw_ideas"]}


# ---------------------------------------------------------------------------
# Node: save_ideas
# ---------------------------------------------------------------------------

async def save_ideas(state: IdeaGenState) -> IdeaGenState:
    ideas = state["filtered_ideas"]
    user_id = state["user_id"]

    logger.info(f"[save_ideas] Saving {len(ideas)} ideas")

    from core.angle.validator import validate_angle

    saved_ideas = []
    for idea in ideas:
        angle = idea.get("angle", "")
        if angle:
            idea_source_ids = idea.get("source_ids", []) or []
            source_summaries = []
            for src_id in idea_source_ids:
                sid_str = str(src_id).replace("source:", "")
                row = await db_fetchrow(
                    "SELECT content FROM source_insight WHERE source_id = $id::uuid AND insight_type = 'summary'",
                    {"id": sid_str},
                )
                if row and row.get("content"):
                    source_summaries.append(row["content"])

            result = await validate_angle(angle, source_summaries=source_summaries or None)
            if not result.valid:
                logger.info(f"[save_ideas] DROP (angle invalid): {angle[:80]} — {result.reason}")
                continue

        # Coerce source_ids to UUIDs (uuid[] column)
        from uuid import UUID
        raw_ids = idea.get("source_ids", []) or []
        source_ids = []
        for sid in raw_ids:
            sid_str = str(sid).replace("source:", "")
            try:
                source_ids.append(UUID(sid_str))
            except (ValueError, TypeError):
                logger.warning(f"[save_ideas] Skipping invalid source_id: {sid!r}")

        fmt = idea.get("format", "")
        if fmt not in {"narrative_drift", "clarity_engine", "momentum_loop", "exploration_engine"}:
            fmt = "clarity_engine"

        await db_execute(
            """
            INSERT INTO show_idea
                (user_id, angle, idea_type, format, source_ids, generated)
            VALUES
                ($user_id, $angle, $idea_type, $format, $source_ids, false)
            """,
            {
                "user_id": user_id,
                "angle": idea.get("angle", ""),
                "idea_type": idea.get("type", "standalone"),
                "format": fmt,
                "source_ids": source_ids,
            },
        )

    logger.info(f"[save_ideas] Done — {len(ideas)} ideas written to show_idea table")
    return {**state, "saved_count": len(ideas)}


# ---------------------------------------------------------------------------
# Node: auto_generate
# ---------------------------------------------------------------------------

async def auto_generate(state: IdeaGenState) -> IdeaGenState:
    user_id = state["user_id"]
    import uuid as _uuid

    new_ideas = await db_query(
        """
        SELECT id, format, source_ids, angle
        FROM show_idea
        WHERE user_id = $user_id
          AND generated = false
        """,
        {"user_id": user_id},
    )

    from core.queue import enqueue
    from studio.formats import FORMATS

    count = 0
    for idea in (new_ideas or []):
        fmt = idea["format"]
        if fmt not in FORMATS:
            fmt = "clarity_engine"

        episode_id = str(_uuid.uuid4())
        try:
            await db_execute(
                """
                INSERT INTO episode
                    (id, user_id, show_name, show_idea_id, editorial_direction,
                     length_minutes, speaker_override, source_ids, status)
                VALUES
                    ($id::uuid, $user_id, $show, $idea_id::uuid, $direction,
                     NULL, NULL, $source_ids, 'queued')
                """,
                {
                    "id": episode_id,
                    "user_id": user_id,
                    "show": fmt,
                    "idea_id": str(idea["id"]),
                    "direction": idea.get("angle", ""),
                    "source_ids": idea.get("source_ids") or [],
                },
            )
            await db_execute(
                "UPDATE show_idea SET generated = true WHERE id = $id::uuid",
                {"id": str(idea["id"])},
            )
            await enqueue(
                type="generate_episode",
                payload={"episode_id": episode_id, "user_id": user_id},
                user_id=user_id,
            )
            logger.info(f"[auto_generate] Enqueued episode {episode_id} for idea {idea['id']}")
            count += 1
        except Exception as e:
            logger.warning(f"[auto_generate] Failed to create episode for idea {idea['id']}: {e}")

    logger.info(f"[auto_generate] {count} episodes enqueued")
    return {**state, "auto_generated_count": count}


# ---------------------------------------------------------------------------
# Build graph
# ---------------------------------------------------------------------------

def build_graph():
    graph = StateGraph(IdeaGenState)

    graph.add_node("load_archive", load_archive)
    graph.add_node("cluster_sources", cluster_sources)
    graph.add_node("diff_clusters", diff_clusters)
    graph.add_node("evaluate_ideas", evaluate_ideas)
    graph.add_node("filter_covered", filter_covered)
    graph.add_node("save_ideas", save_ideas)
    graph.add_node("auto_generate", auto_generate)

    graph.set_entry_point("load_archive")
    graph.add_edge("load_archive", "cluster_sources")
    graph.add_edge("cluster_sources", "diff_clusters")
    graph.add_edge("diff_clusters", "evaluate_ideas")
    graph.add_edge("evaluate_ideas", "filter_covered")
    graph.add_edge("filter_covered", "save_ideas")
    graph.add_edge("save_ideas", "auto_generate")
    graph.add_edge("auto_generate", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def run_idea_generator(user_id: str = "default"):
    logger.info(f"Starting idea generator for user: {user_id}")
    graph = build_graph()
    result = await graph.ainvoke({
        "user_id": user_id,
        "sources": [],
        "clusters": [],
        "raw_ideas": [],
        "filtered_ideas": [],
        "saved_count": 0,
        "auto_generated_count": 0,
    })
    logger.info(f"Idea generator complete — {result['saved_count']} ideas saved")
    return result
