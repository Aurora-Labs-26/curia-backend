"""
intelligence/clustering.py
Shared clustering utilities — cosine similarity + source similarity search.
Extracted from idea_generator.cluster_sources so the eval server can reuse them.
"""

from core.db.connection import db_query, db_execute, db_fetchrow


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x ** 2 for x in a) ** 0.5
    mag_b = sum(x ** 2 for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


async def find_similar_sources(
    source_id: str,
    threshold: float = 0.50,
    limit: int = 20,
) -> list[dict]:
    """
    Find sources similar to `source_id` by comparing primitive embeddings.
    Returns [{source_id, title, url, score}] sorted by score desc.
    Threshold is the minimum score to include (low default — let the UI filter).
    """
    from core.embeddings import get_embedding_column

    bare_id = source_id.replace("source:", "")
    emb_col = get_embedding_column()

    target_row = await db_fetchrow(
        f"SELECT {emb_col} FROM source_primitive_embedding WHERE source_id = $sid::uuid",
        {"sid": bare_id},
    )
    if not target_row or target_row.get(emb_col) is None:
        return []

    target_emb = list(target_row[emb_col])

    rows = await db_query(
        f"""
        SELECT spe.source_id, s.title, s.url, spe.{emb_col}
        FROM source_primitive_embedding spe
        JOIN source s ON s.id = spe.source_id
        WHERE spe.source_id != $sid::uuid AND spe.{emb_col} IS NOT NULL
        """,
        {"sid": bare_id},
    )
    if not rows:
        return []

    cached = await db_query(
        """
        SELECT source_b::text, score FROM source_similarity
        WHERE source_a = $sid::uuid
        UNION ALL
        SELECT source_a::text, score FROM source_similarity
        WHERE source_b = $sid::uuid
        """,
        {"sid": bare_id},
    )
    cache = {r["source_b"]: r["score"] for r in (cached or [])}

    results = []
    for row in rows:
        other_id = str(row["source_id"])
        if other_id in cache:
            score = cache[other_id]
        else:
            other_emb = list(row[emb_col])
            score = cosine(target_emb, other_emb)
            try:
                await db_execute(
                    """
                    INSERT INTO source_similarity (source_a, source_b, score)
                    VALUES ($a::uuid, $b::uuid, $score)
                    ON CONFLICT DO NOTHING
                    """,
                    {"a": bare_id, "b": other_id, "score": score},
                )
            except Exception:
                pass

        if score >= threshold:
            results.append({
                "source_id": other_id,
                "title": row.get("title") or "Untitled",
                "url": row.get("url") or "",
                "score": round(float(score), 3),
            })

    results.sort(key=lambda r: -r["score"])
    return results[:limit]
