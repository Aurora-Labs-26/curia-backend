"""
worker/handlers/ingest.py
Worker handler for the 'ingest' job type.

Payload: { "source_id": "<uuid>", "user_id": "<id>", "url": "<url>" }
The source row already exists (created by API). Worker scrapes → transforms → embeds,
then enqueues the cluster pipeline (generate_ideas), which writes show_idea rows.

Episodes are never created here (ShowIdeas+Streaming.md): the pipeline stops at
show_idea; the user picks an idea in the app to generate an episode.

Seed short-circuit: if the URL is a known seed URL, skip the full pipeline and
promote the placeholder source in place + copy the pre-baked episode to the user
instantly. This deliberately bypasses the ideas flow — onboarding needs instant
gratification (see "Seed Exception" in ShowIdeas+Streaming.md).
"""

import uuid as _uuid

from loguru import logger

from core.db.connection import db_execute, db_fetchrow
from core.ingest import process_source
from core.queue import enqueue
from core.seeds import find_seed


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
# Ingest handler
# ---------------------------------------------------------------------------

async def handle_ingest(payload: dict) -> None:
    source_id = payload.get("source_id")
    if not source_id:
        raise ValueError("ingest job: missing source_id in payload")

    attempt = payload.get("__attempt__", 1)
    max_attempts = payload.get("__max_attempts__", 1)
    is_final_attempt = attempt >= max_attempts
    url = payload.get("url", "")
    user_id_from_payload = payload.get("user_id", "")

    logger.info(f"[handle_ingest] source_id={source_id} attempt={attempt}/{max_attempts}")

    # ── Seed short-circuit ───────────────────────────────────────────────────
    if url and user_id_from_payload:
        short_circuited = await _attach_seed_to_user(
            user_id=user_id_from_payload, url=url, source_id=source_id
        )
        if short_circuited:
            logger.info(f"[handle_ingest] seed short-circuit complete for url={url}")
            return

    # ── Normal pipeline: scrape → transform → embed ──────────────────────────
    await process_source(source_id=source_id, is_final_attempt=is_final_attempt)

    source_row = await db_fetchrow(
        "SELECT user_id FROM source WHERE id = $source_id::uuid",
        {"source_id": source_id},
    )
    if not source_row:
        logger.warning(f"[handle_ingest] could not find source row for source_id={source_id}; skipping generation")
        return

    user_id = source_row["user_id"]

    # ── Cluster pipeline → show_idea rows. Dedup against QUEUED jobs only:
    #    a queued run will see this source when it starts. A RUNNING run loaded
    #    the archive before this source became ready, so it must not absorb the
    #    dedup — otherwise this source's ideas never materialise. At most one
    #    queued run ever accumulates behind a running one.
    existing_job = await db_fetchrow(
        """
        SELECT id FROM jobs
        WHERE type = 'generate_ideas'
          AND user_id = $user_id
          AND status = 'queued'
        LIMIT 1
        """,
        {"user_id": user_id},
    )
    if existing_job:
        logger.info(f"[handle_ingest] generate_ideas already queued for user_id={user_id}; skipping")
        return

    job_id = await enqueue(
        type="generate_ideas",
        payload={"user_id": user_id},
        user_id=user_id,
    )
    logger.info(f"[handle_ingest] enqueued generate_ideas job_id={job_id} for user_id={user_id}")
