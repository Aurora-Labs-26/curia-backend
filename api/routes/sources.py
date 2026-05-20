"""
api/routes/sources.py
Source CRUD — POST creates a row + enqueues the ingest job.
GET /sources, GET /sources/:id, DELETE /sources/:id.
"""

import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from fastapi.responses import Response

from api.auth import current_user_id
from api.schemas import (
    CreateJobResponse,
    CreateSourceRequest,
    EpisodeSourceObject,
    EpisodeSummary,
    SourceDetail,
    SourceSummary,
)
from core.db.connection import db_execute, db_fetchrow, db_query
from core.ingest import get_or_create_source
from core.queue import enqueue

router = APIRouter()


@router.post("/sources", response_model=CreateJobResponse, status_code=202)
async def create_source(
    req: CreateSourceRequest, user_id: str = Depends(current_user_id)
) -> CreateJobResponse:
    """
    Idempotent on (user_id, url): reusing an existing source if present, otherwise
    inserting a new placeholder + enqueuing an 'ingest' job.

    Worker scrapes + transforms + embeds; status moves queued → scraping → ... → ready.
    """
    url_str = str(req.url)
    logger.info(f"[create_source] url={url_str[-40:]} auto_generate={req.auto_generate}")
    source_id = await get_or_create_source(url=url_str, user_id=user_id)

    # Fetch the row's status to decide whether to enqueue a fresh job.
    row = await db_fetchrow(
        "SELECT status FROM source WHERE id = $id::uuid",
        {"id": source_id},
    )
    status = row["status"] if row else "queued"

    job_id = None
    # Re-enqueue when failed or never-ingested. If already running/ready, skip.
    if status in ("queued", "failed"):
        existing_job = await db_fetchrow(
            """
            SELECT id
            FROM jobs
            WHERE type = 'ingest'
              AND status IN ('queued', 'running')
              AND payload->>'source_id' = $source_id
              AND user_id = $user_id
            ORDER BY created_at ASC
            LIMIT 1
            """,
            {"source_id": source_id, "user_id": user_id},
        )
        if existing_job:
            job_id = existing_job["id"]
            logger.info(f"[create_source] reusing ingest job={str(job_id)[:8]} source={source_id[:8]}")
        else:
            job_id = await enqueue(
                type="ingest",
                payload={
                    "source_id": source_id,
                    "user_id": user_id,
                    "url": url_str,
                    "auto_generate": req.auto_generate,
                },
                user_id=user_id,
            )
    return CreateJobResponse(
        id=uuid.UUID(source_id),
        status=status,
        job_id=job_id,
    )


@router.get("/sources", response_model=list[SourceSummary])
async def list_sources(
    user_id: str = Depends(current_user_id),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, le=500),
) -> list[SourceSummary]:
    if status:
        rows = await db_query(
            """
            SELECT s.id, s.title, s.url, s.status, s.created_at, s.error,
                   (SELECT COUNT(*) FROM episode e WHERE s.id = ANY(e.source_ids) AND e.user_id = s.user_id) AS covered_in
            FROM source s
            WHERE s.user_id = $user_id AND s.status = $status AND s.hidden = false
            ORDER BY s.created_at DESC LIMIT $limit
            """,
            {"user_id": user_id, "status": status, "limit": limit},
        )
    else:
        rows = await db_query(
            """
            SELECT s.id, s.title, s.url, s.status, s.created_at, s.error,
                   (SELECT COUNT(*) FROM episode e WHERE s.id = ANY(e.source_ids) AND e.user_id = s.user_id) AS covered_in
            FROM source s
            WHERE s.user_id = $user_id AND s.hidden = false
            ORDER BY s.created_at DESC LIMIT $limit
            """,
            {"user_id": user_id, "limit": limit},
        )
    return [SourceSummary(**r) for r in rows]


@router.post("/sources/clear-failed", status_code=200)
async def clear_failed_sources(user_id: str = Depends(current_user_id)) -> dict:
    """
    Soft-hide all of the user's failed sources. Called by the client on app
    cold-start so last session's failures disappear from the pile. Rows are
    kept in the DB (hidden = true) for debugging/analytics, not deleted.
    """
    rows = await db_query(
        """
        UPDATE source SET hidden = true, updated_at = now()
        WHERE user_id = $user_id AND status = 'failed' AND hidden = false
        RETURNING id
        """,
        {"user_id": user_id},
    )
    logger.info(f"[clear_failed] soft-hid {len(rows)} failed sources for user {user_id}")
    return {"cleared": len(rows)}


@router.get("/sources/{source_id}", response_model=SourceDetail)
async def get_source(
    source_id: uuid.UUID, user_id: str = Depends(current_user_id)
) -> SourceDetail:
    row = await db_fetchrow(
        """
        SELECT id, title, url, status, created_at, error, full_text
        FROM source WHERE id = $id::uuid AND user_id = $user_id
        """,
        {"id": str(source_id), "user_id": user_id},
    )
    if not row:
        raise HTTPException(404, "source not found")

    insight_rows = await db_query(
        """
        SELECT insight_type, content
        FROM source_insight
        WHERE source_id = $id::uuid
        """,
        {"id": str(source_id)},
    )
    insights = {r["insight_type"]: r.get("content") for r in insight_rows}
    return SourceDetail(**row, insights=insights)


@router.get("/sources/{source_id}/episodes", response_model=list[EpisodeSummary])
async def list_episodes_for_source(
    source_id: uuid.UUID, user_id: str = Depends(current_user_id)
) -> list[EpisodeSummary]:
    rows = await db_query(
        """
        SELECT id, show_name, title, status, created_at, error, quality_score,
               length_minutes, speaker_override, source_ids
        FROM episode
        WHERE user_id = $user_id AND source_ids @> ARRAY[$source_id]::uuid[]
        ORDER BY created_at DESC
        """,
        {"user_id": user_id, "source_id": source_id},
    )

    seen: set[str] = set()
    all_source_uuids: list[uuid.UUID] = []
    for r in rows:
        for sid in (r.get("source_ids") or []):
            key = str(sid)
            if key in seen:
                continue
            seen.add(key)
            try:
                all_source_uuids.append(uuid.UUID(key))
            except (ValueError, TypeError):
                pass

    source_map: dict[str, EpisodeSourceObject] = {}
    if all_source_uuids:
        src_rows = await db_query(
            """
            SELECT id, url, title FROM source
            WHERE id = ANY($ids) AND user_id = $user_id
            """,
            {"ids": all_source_uuids, "user_id": user_id},
        )
        for s in src_rows:
            try:
                domain = urlparse(s["url"]).hostname or s["url"]
                domain = domain.removeprefix("www.")
            except Exception:
                domain = s["url"]
            source_map[str(s["id"])] = EpisodeSourceObject(
                id=s["id"], domain=domain, title=s["title"]
            )

    result = []
    for r in rows:
        data = dict(r)
        data["source_objects"] = [
            source_map[str(sid)]
            for sid in (data.get("source_ids") or [])
            if str(sid) in source_map
        ]
        result.append(EpisodeSummary(**data))
    return result


@router.delete("/sources/{source_id}", status_code=204, response_class=Response)
async def delete_source(
    source_id: uuid.UUID, user_id: str = Depends(current_user_id)
) -> Response:
    result = await db_execute(
        "DELETE FROM source WHERE id = $id::uuid AND user_id = $user_id",
        {"id": str(source_id), "user_id": user_id},
    )
    # asyncpg's execute returns "DELETE <n>" — we don't bother parsing for v1
    return Response(status_code=204)
