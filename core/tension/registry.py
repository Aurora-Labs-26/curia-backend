"""
core/tension/registry.py
Tension registry — canonical contested questions as first-class rows.

At ingest, a source's stance card yields a canonical, domain-free tension.
`upsert_tension` snaps it onto an existing registry row when the embedding is
close enough (the same question phrased differently), else creates a new row.
Matching downstream is then exact tension_id equality — no fuzzy comparison at
selection time.
"""

from __future__ import annotations

from loguru import logger

from core.db.connection import db_execute, db_fetchrow

# Cosine similarity above which a new canonical tension is considered the same
# question as an existing registry row. Calibrated 2026-07-22 on the backfilled
# prod registry: at 0.85 the graph fragmented completely (0 intra-user shared
# tensions); measured same-question pairs ("military escalation vs diplomatic
# restraint" ~ "…vs diplomatic de-escalation") cluster at 0.70-0.84 with
# text-embedding-3-small on short canonical phrases. 0.72 captures them; a
# missed snap just splits a tension (mergeable later), a bad snap pollutes.
SNAP_THRESHOLD = 0.72

VALID_POLARITIES = {"side_a", "side_b", "neutral"}


async def upsert_tension(canonical: str, embedding: list[float]) -> str | None:
    """Snap-or-create. Returns tension_id, or None if inputs are unusable."""
    canonical = (canonical or "").strip()
    if not canonical or canonical.lower() == "none" or not embedding:
        return None

    from core.embeddings import get_embedding_column
    col = get_embedding_column()

    nearest = await db_fetchrow(
        f"""
        SELECT id, canonical, 1 - ({col} <=> $emb::vector) AS score
        FROM tension
        WHERE {col} IS NOT NULL
        ORDER BY {col} <=> $emb::vector
        LIMIT 1
        """,
        {"emb": embedding},
    )
    if nearest and float(nearest["score"]) >= SNAP_THRESHOLD:
        await db_execute(
            "UPDATE tension SET source_count = source_count + 1 WHERE id = $id::uuid",
            {"id": str(nearest["id"])},
        )
        logger.debug(f"[tension] snapped '{canonical[:50]}' -> '{nearest['canonical'][:50]}' ({nearest['score']:.2f})")
        return str(nearest["id"])

    row = await db_fetchrow(
        f"""
        INSERT INTO tension (canonical, {col}, source_count)
        VALUES ($canonical, $emb::vector, 1)
        RETURNING id
        """,
        {"canonical": canonical, "emb": embedding},
    )
    logger.debug(f"[tension] new registry row: '{canonical[:60]}'")
    return str(row["id"]) if row else None


async def link_source(
    source_id: str, tension_id: str, polarity: str, confidence: str
) -> None:
    """Idempotent source ↔ tension link with the author's polarity."""
    if polarity not in VALID_POLARITIES:
        polarity = "neutral"
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"
    await db_execute(
        """
        INSERT INTO source_tension (source_id, tension_id, polarity, confidence)
        VALUES ($sid::uuid, $tid::uuid, $polarity, $confidence)
        ON CONFLICT (source_id, tension_id)
        DO UPDATE SET polarity = EXCLUDED.polarity, confidence = EXCLUDED.confidence
        """,
        {"sid": source_id, "tid": tension_id, "polarity": polarity, "confidence": confidence},
    )
