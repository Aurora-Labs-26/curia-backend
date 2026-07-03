"""
worker/handlers/outline_idea.py
Worker handler for the 'outline_idea' job type (ShowIdeas+Streaming.md).

Payload: { "idea_id": "<uuid>", "user_id": "<id>" }

Generates the episode outline for a show_idea and caches it on the row
(title, description, duration_estimate_seconds, outline, chapters) so the
idea detail sheet can render it and episode generation can skip the
outlining stage.

Idempotent: no-op if the idea already has a cached outline. Safe to race
with generate_episode — worst case is one redundant outline LLM call.
"""

from __future__ import annotations

import asyncio
import json

from loguru import logger

from core.db.connection import db_execute, db_fetchrow, db_query
from core.kb import load_kb
from studio.briefing_builder import build_briefing_packet, briefing_packet_to_str

# Rough words→seconds for the outline-time estimate: no transcript exists yet,
# so we go via the format's word budget. ~6 chars/word (spaces included) against
# the TTS chars/sec rate used by the transcript-time estimator keeps the two
# estimates consistent.
CHARS_PER_WORD = 6.0


def _estimate_from_target_words(target_words: int, format_name: str) -> int:
    from studio.generator import TTS_CHARS_PER_SECOND, INTRO_FULL_MS
    from shows.profiles import SHOW_PROFILES

    seconds = target_words * CHARS_PER_WORD / TTS_CHARS_PER_SECOND
    profile = SHOW_PROFILES.get(format_name)
    if profile is not None and profile.intro_audio_path:
        seconds += INTRO_FULL_MS / 1000
    return max(60, int(round(seconds)))


async def handle_outline_idea(payload: dict) -> None:
    idea_id = payload.get("idea_id")
    if not idea_id:
        raise ValueError("outline_idea job: missing idea_id in payload")

    idea = await db_fetchrow(
        """
        SELECT id, user_id, angle, format, source_ids, outline
        FROM show_idea WHERE id = $id::uuid
        """,
        {"id": idea_id},
    )
    if not idea:
        raise ValueError(f"show_idea {idea_id} not found")

    # Idempotency: cached outline wins, always.
    if idea.get("outline"):
        logger.info(f"[outline_idea] {idea_id} already has cached outline — no-op")
        return

    user_id = idea["user_id"]
    format_name = idea["format"]
    source_ids = [str(s) for s in (idea.get("source_ids") or [])]
    if not source_ids:
        raise ValueError(f"show_idea {idea_id} has no source_ids")

    # Load sources + insights (same shape build_briefing_packet expects)
    src_rows = await db_query(
        "SELECT id, title FROM source WHERE id = ANY($ids::uuid[])",
        {"ids": source_ids},
    )
    sources = [{"id": str(r["id"]), "title": r.get("title") or "Untitled"} for r in (src_rows or [])]
    if not sources:
        raise ValueError(f"show_idea {idea_id}: none of its sources exist")

    insight_rows = await db_query(
        "SELECT source_id, insight_type, content FROM source_insight WHERE source_id = ANY($ids::uuid[])",
        {"ids": source_ids},
    )
    insights: dict[str, dict] = {}
    for r in (insight_rows or []):
        sid = str(r["source_id"])
        insights.setdefault(sid, {})[r["insight_type"]] = r.get("content")

    try:
        user_kb = await load_kb(user_id)
    except Exception as e:
        logger.warning(f"[outline_idea] could not load KB for {user_id}: {e}; proceeding without")
        user_kb = None

    packet = build_briefing_packet(
        format_name=format_name,
        sources=sources,
        insights=insights,
        editorial_direction=idea.get("angle") or "",
        user_kb=user_kb,
    )
    briefing = briefing_packet_to_str(packet)

    # Outline LLM call (sync DSPy — offload to thread pool)
    from studio.generator import generate_outline, _derive_display_fields
    loop = asyncio.get_running_loop()
    outline = await loop.run_in_executor(None, generate_outline, briefing, format_name)

    target_words = packet.get("episode_constraints", {}).get("target_words", 1500)
    duration_estimate = _estimate_from_target_words(target_words, format_name)
    description, chapters = _derive_display_fields(outline, duration_estimate)
    title = outline.get("title") or format_name

    await db_execute(
        """
        UPDATE show_idea
        SET title = $title,
            description = $description,
            duration_estimate_seconds = $duration_estimate,
            outline = $outline::jsonb,
            chapters = $chapters::jsonb
        WHERE id = $id::uuid
        """,
        {
            "id": idea_id,
            "title": title,
            "description": description,
            "duration_estimate": duration_estimate,
            "outline": json.dumps(outline),
            "chapters": json.dumps(chapters),
        },
    )
    logger.info(
        f"[outline_idea] cached outline for idea {idea_id}: "
        f"'{title}' (est {duration_estimate}s, {len(chapters)} chapters)"
    )
