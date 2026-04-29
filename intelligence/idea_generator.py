"""
intelligence/idea_generator.py
LangGraph workflow — generates show ideas from the archive.
Runs in background, writes to show_ideas table.

Workflow:
  load_archive → cluster_sources → evaluate_ideas → filter_covered → save_ideas
"""

import json
import os
from typing import TypedDict

import anthropic
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from loguru import logger

from core.db.connection import db_query
from core.embeddings import get_embedding

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../.env"))

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SIMILARITY_THRESHOLD = 0.70   # min cosine score on primitive embeddings to form a clique
MAX_CLUSTER_SIZE = 5          # cap cluster size to keep episodes focused


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class IdeaGenState(TypedDict):
    user_id: str
    sources: list[dict]           # [{id, title, insights: {type: content}}]
    clusters: list[list[str]]     # groups of source_ids
    raw_ideas: list[dict]         # ideas from evaluate_ideas
    filtered_ideas: list[dict]    # ideas passed to save
    saved_count: int


# ---------------------------------------------------------------------------
# Node: load_archive
# ---------------------------------------------------------------------------

async def load_archive(state: IdeaGenState) -> IdeaGenState:
    user_id = state["user_id"]
    logger.info(f"[load_archive] Loading archive for user {user_id}")

    # Fetch all sources
    sources_raw = await db_query("SELECT id, title FROM source", {})

    # Fetch all insights
    insights_raw = await db_query(
        "SELECT source_id, insight_type, content FROM source_insight", {}
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

    return {**state, "sources": sources}


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
    source_embeddings: dict[str, list[float]] = {}
    for source in sources:
        sid = source["id"]
        bare_sid = sid.replace("source:", "")
        result = await db_query(
            "SELECT embedding FROM source_primitive_embedding WHERE source_id = $sid",
            {"sid": bare_sid}
        )
        if result and result[0].get("embedding"):
            source_embeddings[sid] = result[0]["embedding"]

    logger.info(f"[cluster_sources] Got primitive embeddings for {len(source_embeddings)} sources")

    # Cosine similarity
    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = sum(x ** 2 for x in a) ** 0.5
        mag_b = sum(x ** 2 for x in b) ** 0.5
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    ids = list(source_embeddings.keys())

    # Precompute all pairwise scores
    scores = {}
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            s = cosine(source_embeddings[ids[i]], source_embeddings[ids[j]])
            scores[(ids[i], ids[j])] = s
            scores[(ids[j], ids[i])] = s

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

    logger.info(f"[cluster_sources] {len(clusters)} clusters formed")
    return {**state, "clusters": clusters}


# ---------------------------------------------------------------------------
# Node: evaluate_ideas
# ---------------------------------------------------------------------------

BATCH_EVAL_PROMPT = """You generate podcast episode ideas for a single-host audio show.

Each group contains one or more articles with extracted insights. Your job is to find the most interesting, non-obvious angle that could sustain a full episode — not a summary of the articles, but a genuine idea the material makes possible.

A good angle:
- Makes a specific claim or observation, not a vague topic
- Has tension, surprise, or something the listener wouldn't already assume
- Can be explored for 10-12 minutes without exhausting itself

Each group has a GROUP_ID, a type (CLUSTER or STANDALONE), and one or more articles with insights.
For each group, output one angle. You MUST include the group_id exactly as given.
Only skip a group if it has no insights at all.

Also recommend the format that best fits the angle and material:
- narrative_drift: slow, atmospheric, open-ended — for personal, emotional, or reflective material
- clarity_engine: structured, rising — for explaining a mechanism, system, or counterintuitive fact
- momentum_loop: fast, punchy — for actionable ideas with multiple payoffs and high energy
- exploration_engine: analytical — for ideas with strong tension, counterpoints, or a reframe at the end

Output ONLY a valid JSON array. No prose, no markdown:
[
  {
    "group_id": "the exact group_id from the input",
    "type": "standalone" or "cluster",
    "angle": "one sentence — what this episode is actually about",
    "format": "narrative_drift" or "clarity_engine" or "momentum_loop" or "exploration_engine"
  }
]"""


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


async def evaluate_ideas(state: IdeaGenState) -> IdeaGenState:
    clusters = state["clusters"]
    sources = state["sources"]
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

    # Build batch prompt with group_ids embedded
    human = f"Generate one episode idea per group below. Total groups: {len(groups)}\n\n"
    for group_id, (_, idea_type, cluster_sources) in groups.items():
        label = idea_type.upper()
        human += format_group(group_id, label, cluster_sources) + "\n"

    logger.info(f"[evaluate_ideas] Sending batch of {len(groups)} groups to Haiku")

    raw_ideas = []
    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            system=BATCH_EVAL_PROMPT,
            messages=[{"role": "user", "content": human}]
        )
        raw = message.content[0].text.strip()
        ideas = parse_json_response(raw)

        for idea in ideas:
            group_id = idea.get("group_id")
            if not group_id or group_id not in groups:
                logger.warning(f"[evaluate_ideas] Unknown group_id in response: {group_id}")
                continue
            source_ids, idea_type, _ = groups[group_id]
            idea["source_ids"] = source_ids
            idea["type"] = idea_type
            raw_ideas.append(idea)
            logger.info(f"[evaluate_ideas] KEEP [{idea_type}]: {idea.get('angle', '?')[:80]}")

    except Exception as e:
        logger.warning(f"[evaluate_ideas] Batch call failed: {e} — falling back to per-group calls")
        for group_id, (source_ids, idea_type, cluster_sources) in groups.items():
            try:
                msg = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=256,
                    system="""You generate a single podcast episode idea for a single-host audio show.

Given one or more articles with insights, find the most interesting non-obvious angle — not a summary, but a genuine idea the material makes possible.

A good angle makes a specific claim, has tension or surprise, and can sustain 10-12 minutes.

Also recommend the best format:
- narrative_drift: slow, atmospheric, open-ended — personal or reflective material
- clarity_engine: structured, rising — explaining a mechanism or counterintuitive fact
- momentum_loop: fast, punchy — actionable ideas with multiple payoffs
- exploration_engine: analytical — tension, counterpoints, or a reframe at the end

Output ONLY valid JSON: {"type": "standalone", "angle": "one sentence", "format": "narrative_drift or clarity_engine or momentum_loop or exploration_engine"}""",
                    messages=[{"role": "user", "content": format_group(group_id, idea_type.upper(), cluster_sources)}]
                )
                raw = msg.content[0].text.strip()
                idea = parse_json_response(raw)
                if isinstance(idea, list):
                    idea = idea[0]
                idea.pop("title", None)
                idea["source_ids"] = source_ids
                idea["type"] = idea_type
                raw_ideas.append(idea)
                logger.info(f"[evaluate_ideas] KEEP (fallback) [{idea_type}]: {idea.get('angle', '?')[:80]}")
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

    # Clear old ungenerated ideas for this user first
    await db_query(
        "DELETE show_idea WHERE user_id = $user_id AND generated = false",
        {"user_id": user_id}
    )

    for idea in ideas:
        await db_query(
            """CREATE show_idea SET
                user_id = $user_id,
                angle = $angle,
                idea_type = $idea_type,
                format = $format,
                source_ids = $source_ids,
                generated = false,
                created_at = time::now()""",
            {
                "user_id": user_id,
                "angle": idea.get("angle", ""),
                "idea_type": idea.get("type", "standalone"),
                "format": idea.get("format", ""),
                "source_ids": idea.get("source_ids", []),
            }
        )

    logger.info(f"[save_ideas] Done — {len(ideas)} ideas written to show_idea table")
    return {**state, "saved_count": len(ideas)}


# ---------------------------------------------------------------------------
# Build graph
# ---------------------------------------------------------------------------

def build_graph():
    graph = StateGraph(IdeaGenState)

    graph.add_node("load_archive", load_archive)
    graph.add_node("cluster_sources", cluster_sources)
    graph.add_node("evaluate_ideas", evaluate_ideas)
    graph.add_node("filter_covered", filter_covered)
    graph.add_node("save_ideas", save_ideas)

    graph.set_entry_point("load_archive")
    graph.add_edge("load_archive", "cluster_sources")
    graph.add_edge("cluster_sources", "evaluate_ideas")
    graph.add_edge("evaluate_ideas", "filter_covered")
    graph.add_edge("filter_covered", "save_ideas")
    graph.add_edge("save_ideas", END)

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
    })
    logger.info(f"Idea generator complete — {result['saved_count']} ideas saved")
    return result
