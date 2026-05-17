"""
intelligence/selector.py
Archive-to-brief source selector.
Picks the top 10-12 sources from the archive for a given episode.
"""

import os

from dotenv import load_dotenv
from loguru import logger

from core.db.connection import db_query
from core.embeddings import get_embedding

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../.env"))

USER_POOL_THRESHOLD = 15  # switch from system to user pool at this count


async def count_user_sources(user_id: str) -> int:
    """Count total sources for a user."""
    result = await db_query(
        "SELECT COUNT(*) AS count FROM source WHERE user_id = $user_id",
        {"user_id": user_id},
    )
    try:
        return int(result[0]["count"]) if result else 0
    except Exception:
        return 0


async def get_recent_sources(user_id: str, pool: str, limit: int = 5) -> list[dict]:
    """Pull the most recently added sources for the user."""
    result = await db_query(
        """
        SELECT id, title, url, created_at
        FROM source
        WHERE user_id = $user_id
        ORDER BY created_at DESC
        LIMIT $limit
        """,
        {"user_id": user_id, "limit": limit},
    )
    return result or []


async def vector_search_sources(
    query_embedding: list[float],
    user_id: str,
    pool: str,
    limit: int = 20,
    min_score: float = 0.25,
) -> list[dict]:
    """
    Vector search against source_embedding via pgvector cosine similarity.
    `1 - (a <=> b)` converts pgvector's cosine *distance* into similarity score in [-1, 1].
    Returns chunks ranked by relevance.
    """
    from core.embeddings import get_embedding_column
    col = get_embedding_column()
    result = await db_query(
        f"""
        SELECT
            se.source_id,
            se.chunk_text,
            1 - (se.{col} <=> $query_embedding::vector) AS score
        FROM source_embedding se
        JOIN source s ON s.id = se.source_id
        WHERE s.user_id = $user_id
          AND se.{col} IS NOT NULL
          AND 1 - (se.{col} <=> $query_embedding::vector) > $min_score
        ORDER BY score DESC
        LIMIT $limit
        """,
        {
            "user_id": user_id,
            "query_embedding": query_embedding,
            "min_score": min_score,
            "limit": limit,
        },
    )
    return result or []


async def get_source_insights(source_ids: list[str]) -> dict[str, dict]:
    """
    Fetch all source_insight records for a list of source_ids.
    Returns dict: source_id → {insight_type: content}
    """
    if not source_ids:
        return {}

    # Coerce to UUID strings (no "source:" prefix tolerated either way)
    bare_ids = [str(sid).replace("source:", "") for sid in source_ids]

    result = await db_query(
        """
        SELECT source_id, insight_type, content
        FROM source_insight
        WHERE source_id = ANY($ids::uuid[])
        """,
        {"ids": bare_ids},
    )

    insights: dict[str, dict] = {}
    for row in (result or []):
        sid = str(row["source_id"])
        if sid not in insights:
            insights[sid] = {}
        insights[sid][row["insight_type"]] = row["content"]
    return insights


async def get_sources_by_ids(source_ids: list[str]) -> list[dict]:
    """Fetch source records by IDs."""
    if not source_ids:
        return []
    bare_ids = [str(sid).replace("source:", "") for sid in source_ids]
    result = await db_query(
        "SELECT id, title, url FROM source WHERE id = ANY($ids::uuid[])",
        {"ids": bare_ids},
    )
    return result or []


def format_source_insights(sources: list[dict], insights: dict[str, dict]) -> str:
    """
    Format selected sources + their insights into a plain string.
    Used only for legacy paths and compare UI display.
    New pipeline passes the raw insights dict to briefing_builder instead.
    """
    lines = []
    for i, source in enumerate(sources, 1):
        sid = str(source.get("id", "")).replace("source:", "")
        title = source.get("title", "Untitled")
        source_insights = insights.get(sid, {})

        lines.append(f"Source {i} — {title}")
        for field in ["key_insights", "human_stakes", "core_tensions", "counterpoints", "examples"]:
            if source_insights.get(field):
                lines.append(f"{field}: {source_insights[field]}")
        lines.append("")

    return "\n".join(lines)


def _derive_editorial_direction_from_kb(user_kb) -> str:
    """
    When the caller didn't supply an editorial direction, fall back to KB hints:
    current_obsession (best signal) > active interests joined.
    """
    if user_kb is None:
        return ""
    obsession = user_kb.interests.current_obsession
    if obsession:
        return obsession
    if user_kb.interests.topics:
        return ", ".join(user_kb.interests.topics)
    return ""


async def select_episode_sources(
    user_id: str,
    editorial_direction: str = "",
    n: int = 12,
    user_kb=None,
) -> tuple[list[dict], str]:
    """
    Main selector function.
    Returns (selected_sources, formatted_insights_string).

    KB integration: when editorial_direction is empty and user_kb is provided,
    derive a direction from the user's current_obsession or active interests.
    This way "generate me an episode" without an explicit direction still steers
    by what the user actually cares about.
    """
    if not editorial_direction and user_kb is not None:
        editorial_direction = _derive_editorial_direction_from_kb(user_kb)
        if editorial_direction:
            logger.info(f"Using KB-derived editorial direction: {editorial_direction[:80]}")

    # 1. Determine pool
    user_count = await count_user_sources(user_id)
    pool = "user"  # simplified: always use user pool until system pool is seeded
    logger.info(f"User has {user_count} sources — using '{pool}' pool")

    selected_ids = set()
    selected_sources = []

    # 2. Vector search if editorial direction given
    if editorial_direction:
        query_embedding = await get_embedding(editorial_direction)
        if query_embedding:
            search_results = await vector_search_sources(
                query_embedding=query_embedding,
                user_id=user_id,
                pool=pool,
                limit=20
            )
            # Deduplicate by source_id, keep highest score
            seen = {}
            for row in search_results:
                sid = row["source_id"]
                if sid not in seen or row["score"] > seen[sid]:
                    seen[sid] = row["score"]

            # Sort by score, take top results
            ranked = sorted(seen.items(), key=lambda x: x[1], reverse=True)
            for sid, score in ranked[:n]:
                selected_ids.add(sid)
            logger.info(f"Vector search returned {len(selected_ids)} sources")

    # 3. Always include recent sources
    recent = await get_recent_sources(user_id, pool=pool, limit=5)
    for source in recent:
        sid = str(source.get("id", ""))
        if sid and sid not in selected_ids:
            selected_ids.add(sid)
            selected_sources.append(source)

    # 4. Fetch full source records for vector search results
    vector_ids = [sid for sid in selected_ids if sid not in {str(s.get("id","")) for s in selected_sources}]
    if vector_ids:
        vector_sources = await get_sources_by_ids(vector_ids)
        selected_sources = vector_sources + selected_sources  # relevance first

    # Cap at n
    selected_sources = selected_sources[:n]
    final_ids = [str(s.get("id", "")) for s in selected_sources]

    logger.info(f"Selected {len(selected_sources)} sources for episode brief")

    # 5. Fetch insights
    insights = await get_source_insights(final_ids)

    return selected_sources, insights
