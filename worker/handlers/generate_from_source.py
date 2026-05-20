"""
worker/handlers/generate_from_source.py
Worker handler for the 'generate_from_source' job type.

Payload:
  {
    "user_id":       "<firebase uid>",
    "source_id":     "<uuid>",
    "standalone":    true | false,
    "show_name":     "<backend format name> | null",
    "speaker":       "kenji" | "arjun" | "emeka" | null,
    "length_minutes": 3-30 | null,
    "angle_override": "<text> | null"
  }

Two paths:
  standalone=true  — evaluate this source alone, create one episode with overrides
  standalone=false — run full idea-generator pipeline, apply overrides to any episode
                     that contains this source_id
"""

import uuid as _uuid

from loguru import logger

from core.db.connection import db_execute, db_fetchrow, db_query
from core.queue import enqueue
from intelligence.idea_generator import (
    IdeaGenState,
    build_graph,
    evaluate_ideas,
    format_group,
    has_complete_insights,
    parse_json_response,
)
from studio.formats import FORMATS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_format(fmt: str | None) -> str:
    """Return canonical format name, falling back to clarity_engine."""
    if fmt and fmt in FORMATS:
        return fmt
    return "clarity_engine"


async def _load_source_with_insights(source_id: str, user_id: str) -> dict | None:
    """Load a single source row + its insights for a given user."""
    row = await db_fetchrow(
        "SELECT id, title FROM source WHERE id = $id::uuid AND user_id = $user_id",
        {"id": source_id, "user_id": user_id},
    )
    if not row:
        return None

    insight_rows = await db_query(
        "SELECT insight_type, content FROM source_insight WHERE source_id = $id::uuid",
        {"id": source_id},
    )
    insights = {r["insight_type"]: r.get("content") for r in (insight_rows or [])}
    return {"id": str(row["id"]), "title": row.get("title", "Untitled"), "insights": insights}


async def _create_episode_and_enqueue(
    *,
    user_id: str,
    idea_row: dict,
    show_name: str | None,
    speaker: str | None,
    length_minutes: int | None,
    angle_override: str | None,
) -> str:
    """
    Insert an episode row and enqueue a generate_episode job.
    Returns the new episode_id.
    """
    fmt = _validate_format(show_name or idea_row.get("format"))
    base_direction = idea_row.get("angle", "")
    if angle_override:
        editorial_direction = f"{base_direction}. {angle_override}" if base_direction else angle_override
    else:
        editorial_direction = base_direction

    episode_id = str(_uuid.uuid4())
    idea_id = str(idea_row.get("id")) if idea_row.get("id") else None
    source_ids = idea_row.get("source_ids") or []
    await db_execute(
        """
        INSERT INTO episode
            (id, user_id, show_name, show_idea_id, editorial_direction,
             length_minutes, speaker_override, source_ids, status)
        VALUES
            ($id::uuid, $user_id, $show, $idea_id::uuid, $direction,
             $length_minutes, $speaker_override, $source_ids, 'queued')
        """,
        {
            "id": episode_id,
            "user_id": user_id,
            "show": fmt,
            "idea_id": idea_id,
            "direction": editorial_direction,
            "length_minutes": length_minutes,
            "speaker_override": speaker,
            "source_ids": source_ids,
        },
    )
    if idea_id:
        await db_execute(
            "UPDATE show_idea SET generated = true WHERE id = $id::uuid",
            {"id": idea_id},
        )
    await enqueue(
        type="generate_episode",
        payload={"episode_id": episode_id, "user_id": user_id},
        user_id=user_id,
    )
    logger.info(f"[generate_from_source] episode {episode_id} enqueued")
    return episode_id


# ---------------------------------------------------------------------------
# Standalone path
# ---------------------------------------------------------------------------

async def _run_standalone(
    *,
    user_id: str,
    source_id: str,
    show_name: str | None,
    speaker: str | None,
    length_minutes: int | None,
    angle_override: str | None,
) -> None:
    """
    Evaluate a single source group using the LLM, write a show_idea, then create
    an episode with any overrides applied.
    """
    source = await _load_source_with_insights(source_id, user_id)
    if not source:
        raise ValueError(f"source {source_id} not found for user {user_id}")

    if not has_complete_insights(source):
        logger.warning(f"[generate_from_source] source {source_id} has no key_insights; proceeding anyway")

    group_id = "g0"
    group_text = format_group(group_id, "STANDALONE", [source])

    from core.prompts.idea_evaluation import evaluate_single_idea
    try:
        prediction = evaluate_single_idea(group_text=group_text)
        idea = parse_json_response(prediction.idea_json)
        if isinstance(idea, list):
            idea = idea[0]
    except Exception as e:
        logger.warning(f"[generate_from_source] LLM call failed: {e}; using blank angle")
        idea = {"type": "standalone", "angle": "", "format": "clarity_engine"}

    fmt = _validate_format(show_name or idea.get("format"))
    angle = idea.get("angle", "")

    # Write show_idea (generated=true immediately — no separate auto_generate needed)
    idea_id = str(_uuid.uuid4())
    from uuid import UUID
    source_uuid = UUID(source_id.replace("source:", ""))
    await db_execute(
        """
        INSERT INTO show_idea
            (id, user_id, angle, idea_type, format, source_ids, generated)
        VALUES
            ($id::uuid, $user_id, $angle, 'standalone', $format, $source_ids, true)
        """,
        {
            "id": idea_id,
            "user_id": user_id,
            "angle": angle,
            "format": fmt,
            "source_ids": [source_uuid],
        },
    )

    idea_row = {"id": idea_id, "angle": angle, "format": fmt, "source_ids": [source_uuid]}
    await _create_episode_and_enqueue(
        user_id=user_id,
        idea_row=idea_row,
        show_name=show_name,
        speaker=speaker,
        length_minutes=length_minutes,
        angle_override=angle_override,
    )
    logger.info(f"[generate_from_source] standalone: 1 episode created for source {source_id}")


# ---------------------------------------------------------------------------
# Cluster path
# ---------------------------------------------------------------------------

async def _run_cluster(
    *,
    user_id: str,
    source_id: str,
    show_name: str | None,
    speaker: str | None,
    length_minutes: int | None,
    angle_override: str | None,
) -> None:
    """
    Run the full idea-generator pipeline for this user, then create episodes for
    any new ideas that contain this source_id, with overrides applied.

    New ideas that don't contain this source_id are still created normally via
    the auto_generate node inside the pipeline — no special logic needed here.
    """
    from intelligence.idea_generator import run_idea_generator

    # Run the full pipeline — auto_generate node creates episodes for all new ideas.
    # We then find the episodes that are linked to ideas containing our source_id,
    # and apply the user's overrides by updating those rows.
    result = await run_idea_generator(user_id=user_id)
    logger.info(f"[generate_from_source] cluster path: pipeline done, {result.get('auto_generated_count', 0)} episodes created")

    # Now find show_ideas that contain this source_id and were just created
    # (generated=true means auto_generate already made an episode for them)
    bare_source_id = source_id.replace("source:", "")
    ideas_with_source = await db_query(
        """
        SELECT si.id AS idea_id, e.id AS episode_id
        FROM show_idea si
        JOIN episode e ON e.show_idea_id = si.id
        WHERE si.user_id = $user_id
          AND $sid::uuid = ANY(si.source_ids)
          AND e.status = 'queued'
          AND e.created_at > now() - interval '120 seconds'
        """,
        {"user_id": user_id, "sid": bare_source_id},
    )

    # If nothing matched (source ended up in no cluster, standalone idea was created instead)
    # the auto_generate node already handled it — no overrides to apply.
    if not ideas_with_source:
        logger.info(f"[generate_from_source] cluster path: no recently-queued episodes for source {source_id}; no overrides to apply")
        return

    # Apply overrides to matching episodes
    for row in ideas_with_source:
        episode_id = str(row["episode_id"])
        updates = []
        params: dict = {"id": episode_id}

        if show_name:
            fmt = _validate_format(show_name)
            updates.append("show_name = $show_name")
            params["show_name"] = fmt
        if speaker:
            updates.append("speaker_override = $speaker")
            params["speaker"] = speaker
        if length_minutes:
            updates.append("length_minutes = $length_minutes")
            params["length_minutes"] = length_minutes
        if angle_override:
            updates.append("editorial_direction = editorial_direction || $angle_suffix")
            params["angle_suffix"] = f". {angle_override}"

        if updates:
            set_clause = ", ".join(updates)
            await db_execute(
                f"UPDATE episode SET {set_clause} WHERE id = $id::uuid",
                params,
            )
            logger.info(f"[generate_from_source] cluster path: overrides applied to episode {episode_id}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def handle_generate_from_source(payload: dict) -> None:
    user_id = payload.get("user_id")
    source_id = payload.get("source_id")
    standalone = payload.get("standalone", True)
    show_name = payload.get("show_name")
    speaker = payload.get("speaker")
    length_minutes = payload.get("length_minutes")
    angle_override = payload.get("angle_override")

    if not user_id or not source_id:
        raise ValueError("generate_from_source: missing user_id or source_id in payload")

    logger.info(
        f"[generate_from_source] user={user_id} source={source_id} "
        f"standalone={standalone} show_name={show_name} speaker={speaker}"
    )

    if standalone:
        await _run_standalone(
            user_id=user_id,
            source_id=source_id,
            show_name=show_name,
            speaker=speaker,
            length_minutes=length_minutes,
            angle_override=angle_override,
        )
    else:
        await _run_cluster(
            user_id=user_id,
            source_id=source_id,
            show_name=show_name,
            speaker=speaker,
            length_minutes=length_minutes,
            angle_override=angle_override,
        )
