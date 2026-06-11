"""
worker/handlers/ingest.py
Worker handler for the 'ingest' job type.

Payload: { "source_id": "<uuid>", "user_id": "<id>", "url": "<url>", "standalone": true|false }
The source row already exists (created by API). Worker scrapes → transforms → embeds,
then triggers generation:
  standalone=True  → evaluate this source alone (_run_standalone)
  standalone=False → run full cluster pipeline (generate_ideas)

Seed short-circuit: if the URL is a known seed URL, skip the full pipeline and
promote the placeholder source in place + copy the pre-baked episode to the user instantly.
"""

import uuid as _uuid

from loguru import logger

from core.db.connection import db_execute, db_fetchrow, db_query
from core.ingest import process_source
from core.queue import enqueue
from core.seeds import find_seed
from studio.formats import FORMATS
from intelligence.idea_generator import (
    has_complete_insights,
    format_group,
    parse_json_response,
)


# ---------------------------------------------------------------------------
# Seed short-circuit
# ---------------------------------------------------------------------------

async def _attach_seed_to_user(user_id: str, url: str, source_id: str) -> bool:
    """
    If url is a seed URL and the seed has been set up, promote the placeholder
    source row in place and copy the pre-baked episode to user_id.
    Returns True if short-circuit succeeded, False otherwise.

    Key design decisions:
    - We CANNOT insert a new source row because (user_id, url) is unique — the
      API already created a placeholder with that pair. Instead we UPDATE the
      placeholder in place, copying title/full_text from the seed source.
    - We do NOT set hidden=true on the source. It must remain visible in the pile.
    """
    entry = find_seed(url)
    if not entry:
        return False

    # Use the canonical URL from the SeedEntry for the DB lookup — the payload
    # url may have been normalised (trailing slash stripped, etc.) and won't
    # match the stored key if they differ.
    seed_row = await db_fetchrow(
        "SELECT source_id, episode_id FROM seed_url WHERE url = $url",
        {"url": entry.url},
    )
    if not seed_row or not seed_row["source_id"] or not seed_row["episode_id"]:
        logger.info(f"[handle_ingest] seed URL detected but not yet set up — running normal pipeline for {url}")
        return False

    seed_source_id = str(seed_row["source_id"])
    seed_episode_id = str(seed_row["episode_id"])

    # ── Promote the placeholder source row in place ──────────────────────────
    already_ready = await db_fetchrow(
        "SELECT id FROM source WHERE id = $id::uuid AND status = 'ready' AND is_seed = true",
        {"id": source_id},
    )
    if already_ready:
        logger.info(f"[handle_ingest] placeholder already promoted, skipping: source_id={source_id}")
    else:
        await db_execute(
            """
            UPDATE source
            SET title      = s.title,
                full_text  = s.full_text,
                is_seed    = true,
                status     = 'ready',
                hidden     = false,
                pool       = 'user',
                updated_at = now()
            FROM source s
            WHERE source.id = $placeholder_id::uuid
              AND s.id       = $seed_source_id::uuid
            """,
            {"placeholder_id": source_id, "seed_source_id": seed_source_id},
        )

        # Copy source_insight rows
        await db_execute(
            """
            INSERT INTO source_insight (id, source_id, insight_type, content)
            SELECT gen_random_uuid(), $source_id::uuid, insight_type, content
            FROM source_insight WHERE source_id = $seed_source_id::uuid
            ON CONFLICT DO NOTHING
            """,
            {"source_id": source_id, "seed_source_id": seed_source_id},
        )

        # Copy source_embedding rows
        await db_execute(
            """
            INSERT INTO source_embedding (id, source_id, chunk_index, chunk_text, embedding)
            SELECT gen_random_uuid(), $source_id::uuid, chunk_index, chunk_text, embedding
            FROM source_embedding WHERE source_id = $seed_source_id::uuid
            """,
            {"source_id": source_id, "seed_source_id": seed_source_id},
        )

        # Copy source_primitive_embedding
        await db_execute(
            """
            INSERT INTO source_primitive_embedding (source_id, embedding)
            SELECT $source_id::uuid, embedding
            FROM source_primitive_embedding WHERE source_id = $seed_source_id::uuid
            ON CONFLICT (source_id) DO NOTHING
            """,
            {"source_id": source_id, "seed_source_id": seed_source_id},
        )

        logger.info(f"[handle_ingest] promoted placeholder to seed source: source_id={source_id}")

    # ── Copy seed episode to user ────────────────────────────────────────────
    existing_episode = await db_fetchrow(
        """
        SELECT id FROM episode
        WHERE user_id = $user_id AND $source_id::uuid = ANY(source_ids) AND is_seed = true
        """,
        {"user_id": user_id, "source_id": source_id},
    )
    if existing_episode:
        logger.info(f"[handle_ingest] user already has seed episode, skipping copy")
    else:
        user_episode_id = str(_uuid.uuid4())
        await db_execute(
            """
            INSERT INTO episode
                (id, user_id, show_name, title, transcript, outline, editorial_direction,
                 source_ids, audio_path, audio_url, status, quality_score, tts_timings,
                 duration_seconds, description, chapters, is_seed)
            SELECT
                $new_id::uuid, $user_id, show_name, title, transcript, outline,
                editorial_direction,
                ARRAY[$source_id::uuid], audio_path, audio_url, status,
                quality_score, tts_timings, duration_seconds, description, chapters, true
            FROM episode WHERE id = $seed_episode_id::uuid
            """,
            {
                "new_id": user_episode_id,
                "user_id": user_id,
                "source_id": source_id,
                "seed_episode_id": seed_episode_id,
            },
        )
        logger.info(f"[handle_ingest] copied seed episode to user: episode_id={user_episode_id}")

    return True


# ---------------------------------------------------------------------------
# Standalone generation helpers
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
    logger.info(f"[ingest] episode {episode_id} enqueued")
    return episode_id


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
    Evaluate a single source using the LLM, write a show_idea, then create
    an episode with any overrides applied.
    """
    source = await _load_source_with_insights(source_id, user_id)
    if not source:
        raise ValueError(f"source {source_id} not found for user {user_id}")

    if not has_complete_insights(source):
        logger.warning(f"[ingest] source {source_id} has no key_insights; proceeding anyway")

    group_id = "g0"
    group_text = format_group(group_id, "STANDALONE", [source])

    from core.prompts.idea_evaluation import evaluate_single_idea
    try:
        prediction = evaluate_single_idea(group_text=group_text)
        idea = parse_json_response(prediction.idea_json)
        if isinstance(idea, list):
            idea = idea[0]
    except Exception as e:
        logger.warning(f"[ingest] LLM call failed: {e}; using blank angle")
        idea = {"type": "standalone", "angle": "", "format": "clarity_engine"}

    fmt = _validate_format(show_name or idea.get("format"))
    angle = idea.get("angle", "")

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
    logger.info(f"[ingest] standalone: 1 episode created for source {source_id}")


# ---------------------------------------------------------------------------
# Ingest handler
# ---------------------------------------------------------------------------

async def handle_ingest(payload: dict) -> None:
    source_id = payload.get("source_id")
    if not source_id:
        raise ValueError("ingest job: missing source_id in payload")

    attempt = payload.get("__attempt__", 1)
    max_attempts = payload.get("__max_attempts__", 1)
    is_final_attempt = attempt >= max_attempts
    standalone = payload.get("standalone", True)
    url = payload.get("url", "")
    user_id_from_payload = payload.get("user_id", "")

    logger.info(f"[handle_ingest] source_id={source_id} attempt={attempt}/{max_attempts} standalone={standalone}")

    # ── Idempotency guard (SQS delivers at-least-once) ───────────────────────
    # Baton-aware: a redelivery may mean (a) true duplicate — ingest AND its
    # chained generation both happened → no-op; or (b) dropped baton — ingest
    # finished but the chain-enqueue failed → skip the re-scrape, resume the
    # chain. Mid-states (scraping/…) mean a previous attempt died → full re-run.
    guard_row = await db_fetchrow(
        "SELECT status FROM source WHERE id = $source_id::uuid",
        {"source_id": source_id},
    )
    already_ingested = bool(guard_row and guard_row["status"] == "ready")
    if already_ingested and standalone:
        episode_row = await db_fetchrow(
            "SELECT id FROM episode WHERE source_ids @> ARRAY[$source_id::uuid] LIMIT 1",
            {"source_id": source_id},
        )
        if episode_row:
            logger.info(f"[handle_ingest] source {source_id} ready + episode exists — skipping (duplicate delivery)")
            return
        logger.info(f"[handle_ingest] source {source_id} ready but no episode — resuming dropped chain")

    # ── Seed short-circuit ───────────────────────────────────────────────────
    if url and user_id_from_payload:
        short_circuited = await _attach_seed_to_user(
            user_id=user_id_from_payload, url=url, source_id=source_id
        )
        if short_circuited:
            logger.info(f"[handle_ingest] seed short-circuit complete for url={url}")
            return

    # ── Normal pipeline ──────────────────────────────────────────────────────
    if already_ingested:
        logger.info(f"[handle_ingest] skipping re-ingest of ready source {source_id}; proceeding to chain")
    else:
        try:
            await process_source(source_id=source_id, is_final_attempt=is_final_attempt)
        except Exception as exc:
            from core.errors import PermanentError

            # Mark the source failed when no retry will follow: last attempt, OR a
            # PermanentError (deleted immediately, never retried) on any attempt.
            # Otherwise a 403/404 on attempt 1 leaves the source 'scraping' forever
            # and the app shows a perpetual Queued card.
            if is_final_attempt or isinstance(exc, PermanentError):
                await db_execute(
                    "UPDATE source SET status = 'failed', error = $error WHERE id = $source_id::uuid",
                    {"source_id": source_id, "error": str(exc)},
                )
            raise

    source_row = await db_fetchrow(
        "SELECT user_id FROM source WHERE id = $source_id::uuid",
        {"source_id": source_id},
    )
    if not source_row:
        logger.warning(f"[handle_ingest] could not find source row for source_id={source_id}; skipping generation")
        return

    user_id = source_row["user_id"]

    if standalone:
        logger.info(f"[handle_ingest] standalone=True — running _run_standalone for source_id={source_id}")
        await _run_standalone(
            user_id=user_id,
            source_id=source_id,
            show_name=None,
            speaker=None,
            length_minutes=None,
            angle_override=None,
        )
    else:
        existing_job = await db_fetchrow(
            """
            SELECT id FROM jobs
            WHERE type = 'generate_ideas'
              AND user_id = $user_id
              AND status IN ('queued', 'running')
            LIMIT 1
            """,
            {"user_id": user_id},
        )
        if existing_job:
            logger.info(f"[handle_ingest] generate_ideas already queued/running for user_id={user_id}; skipping")
            return

        job_id = await enqueue(
            type="generate_ideas",
            payload={"user_id": user_id},
            user_id=user_id,
        )
        logger.info(f"[handle_ingest] standalone=False — enqueued generate_ideas job_id={job_id} for user_id={user_id}")
