"""
api/routes/ideas.py
ShowIdeas flow (ShowIdeas+Streaming.md):

  GET  /ideas                — live ideas (generated=false, superseded=false) for the tab
  GET  /ideas/:id            — full idea incl. cached outline (detail sheet)
  POST /ideas/:id/outline    — idempotent outline job enqueue (client polls the job)
  POST /ideas/:id/generate   — create episode from idea (+ optional overrides)
  POST /ideas/generate       — kick off the cluster pipeline manually (QA/debug)
"""

import json
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from api.auth import current_user_id
from api.schemas import (
    CreateJobResponse,
    EpisodeSourceObject,
    GenerateFromIdeaRequest,
    GenerateIdeasResponse,
    OutlineJobResponse,
    ShowIdeaDetail,
    ShowIdeaSummary,
)
from core.db.connection import db_execute, db_fetchrow, db_query
from core.queue import enqueue
from studio.formats import FORMATS, resolve_format_name

router = APIRouter()

_IDEA_FIELDS = """
    id, angle, idea_type, format, source_ids, generated, created_at,
    title, description, duration_estimate_seconds, chapters, superseded
"""


async def _source_objects_for(
    source_id_lists: list[list], user_id: str
) -> dict[str, EpisodeSourceObject]:
    """Batch-fetch source rows for many ideas → {source_id: EpisodeSourceObject}."""
    seen: set[str] = set()
    all_uuids: list[uuid.UUID] = []
    for ids in source_id_lists:
        for sid in (ids or []):
            key = str(sid)
            if key not in seen:
                seen.add(key)
                try:
                    all_uuids.append(uuid.UUID(key))
                except (ValueError, TypeError):
                    pass
    if not all_uuids:
        return {}
    rows = await db_query(
        """
        SELECT id, url, title FROM source
        WHERE id = ANY($ids) AND (user_id = $user_id OR (is_seed = true AND user_id = 'seed'))
        """,
        {"ids": all_uuids, "user_id": user_id},
    )
    result: dict[str, EpisodeSourceObject] = {}
    for s in rows:
        try:
            domain = urlparse(s["url"]).hostname or s["url"]
            domain = domain.removeprefix("www.")
        except Exception:
            domain = s["url"] or ""
        result[str(s["id"])] = EpisodeSourceObject(id=s["id"], domain=domain, title=s["title"])
    return result


def _attach_sources(data: dict, source_map: dict[str, EpisodeSourceObject]) -> dict:
    data["source_objects"] = [
        source_map[str(sid)] for sid in (data.get("source_ids") or []) if str(sid) in source_map
    ]
    return data


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get("/ideas", response_model=list[ShowIdeaSummary])
async def list_ideas(user_id: str = Depends(current_user_id)) -> list[ShowIdeaSummary]:
    """The ideas tab: live ideas only, newest first."""
    rows = await db_query(
        f"""
        SELECT {_IDEA_FIELDS}
        FROM show_idea
        WHERE user_id = $user_id AND generated = false AND superseded = false
        ORDER BY created_at DESC
        LIMIT 100
        """,
        {"user_id": user_id},
    )
    source_map = await _source_objects_for([r.get("source_ids") for r in rows], user_id)
    return [ShowIdeaSummary(**_attach_sources(dict(r), source_map)) for r in rows]


@router.get("/ideas/{idea_id}", response_model=ShowIdeaDetail)
async def get_idea(
    idea_id: uuid.UUID, user_id: str = Depends(current_user_id)
) -> ShowIdeaDetail:
    row = await db_fetchrow(
        f"""
        SELECT {_IDEA_FIELDS}, outline
        FROM show_idea
        WHERE id = $id::uuid AND user_id = $user_id
        """,
        {"id": str(idea_id), "user_id": user_id},
    )
    if not row:
        raise HTTPException(404, "show_idea not found")
    source_map = await _source_objects_for([row.get("source_ids")], user_id)
    return ShowIdeaDetail(**_attach_sources(dict(row), source_map))


# ---------------------------------------------------------------------------
# Outline job (idempotent)
# ---------------------------------------------------------------------------


@router.post("/ideas/{idea_id}/outline", response_model=OutlineJobResponse, status_code=202)
async def request_outline(
    idea_id: uuid.UUID, user_id: str = Depends(current_user_id)
) -> OutlineJobResponse:
    """
    Fired when the detail sheet opens. Idempotent:
    - outline already cached → status='done', no job
    - outline job already queued/running → return that job_id
    - otherwise enqueue a fresh outline_idea job
    """
    row = await db_fetchrow(
        "SELECT id, outline FROM show_idea WHERE id = $id::uuid AND user_id = $user_id",
        {"id": str(idea_id), "user_id": user_id},
    )
    if not row:
        raise HTTPException(404, "show_idea not found")

    if row.get("outline"):
        return OutlineJobResponse(idea_id=idea_id, job_id=None, status="done")

    existing = await db_fetchrow(
        """
        SELECT id FROM jobs
        WHERE type = 'outline_idea'
          AND status IN ('queued', 'running')
          AND payload->>'idea_id' = $idea_id
          AND user_id = $user_id
        ORDER BY created_at ASC
        LIMIT 1
        """,
        {"idea_id": str(idea_id), "user_id": user_id},
    )
    if existing:
        return OutlineJobResponse(idea_id=idea_id, job_id=existing["id"], status="queued")

    job_id = await enqueue(
        type="outline_idea",
        payload={"idea_id": str(idea_id), "user_id": user_id},
        user_id=user_id,
    )
    return OutlineJobResponse(idea_id=idea_id, job_id=job_id, status="queued")


# ---------------------------------------------------------------------------
# Generate episode from idea
# ---------------------------------------------------------------------------


@router.post("/ideas/{idea_id}/generate", response_model=CreateJobResponse, status_code=202)
async def generate_from_idea(
    idea_id: uuid.UUID,
    req: GenerateFromIdeaRequest,
    user_id: str = Depends(current_user_id),
) -> CreateJobResponse:
    """
    User tapped "Generate Episode" on the detail sheet.

    Deliberately does NOT check `superseded` — a user viewing a retired idea
    can still generate from it (superseded only controls list visibility).

    Cached-outline reuse: format/angle/length untouched → the cached outline
    (and its display fields) are copied onto the episode row, so the episode
    skips the outlining stage and the card shows title/duration immediately.
    Speaker overrides never invalidate the cache.
    """
    idea = await db_fetchrow(
        """
        SELECT id, angle, format, source_ids, outline,
               title, description, duration_estimate_seconds, chapters
        FROM show_idea
        WHERE id = $id::uuid AND user_id = $user_id
        """,
        {"id": str(idea_id), "user_id": user_id},
    )
    if not idea:
        raise HTTPException(404, "show_idea not found")

    # Resolve format override (accepts frontend slug or backend name)
    fmt = idea["format"]
    if req.format:
        resolved = resolve_format_name(req.format)
        if resolved not in FORMATS:
            raise HTTPException(422, f"unknown format '{req.format}'")
        fmt = resolved
    if fmt not in FORMATS:
        fmt = "clarity_engine"

    angle = req.angle if req.angle is not None else (idea.get("angle") or "")

    # Outline cache validity: any content-shaping override invalidates it.
    format_changed = fmt != idea["format"]
    angle_changed = req.angle is not None and req.angle.strip() != (idea.get("angle") or "").strip()
    length_changed = req.length_minutes is not None
    reuse_outline = bool(idea.get("outline")) and not (format_changed or angle_changed or length_changed)

    episode_id = str(uuid.uuid4())
    outline_json = None
    if reuse_outline:
        outline_json = idea["outline"]
        if not isinstance(outline_json, str):
            outline_json = json.dumps(outline_json)
    chapters_json = None
    if reuse_outline and idea.get("chapters"):
        chapters_json = idea["chapters"]
        if not isinstance(chapters_json, str):
            chapters_json = json.dumps(chapters_json)

    await db_execute(
        """
        INSERT INTO episode
            (id, user_id, show_name, show_idea_id, editorial_direction,
             length_minutes, speaker_override, speaker_pair, source_ids,
             outline, title, description, duration_estimate_seconds, chapters,
             status)
        VALUES
            ($id::uuid, $user_id, $show, $idea_id::uuid, $direction,
             $length_minutes, $speaker_override, $speaker_pair, $source_ids,
             $outline::jsonb, $title, $description, $duration_estimate, $chapters::jsonb,
             'queued')
        """,
        {
            "id": episode_id,
            "user_id": user_id,
            "show": fmt,
            "idea_id": str(idea_id),
            "direction": angle,
            "length_minutes": req.length_minutes,
            "speaker_override": req.speaker,
            "speaker_pair": req.speaker_pair if not req.speaker else None,
            "source_ids": idea.get("source_ids") or [],
            "outline": outline_json,
            "title": idea.get("title") if reuse_outline else None,
            "description": idea.get("description") if reuse_outline else None,
            "duration_estimate": idea.get("duration_estimate_seconds") if reuse_outline else None,
            "chapters": chapters_json,
        },
    )
    await db_execute(
        "UPDATE show_idea SET generated = true WHERE id = $id::uuid",
        {"id": str(idea_id)},
    )
    job_id = await enqueue(
        type="generate_episode",
        payload={"episode_id": episode_id, "user_id": user_id},
        user_id=user_id,
    )
    logger.info(
        f"[generate_from_idea] episode {episode_id} from idea {idea_id} "
        f"(format={fmt}, reuse_outline={reuse_outline})"
    )
    return CreateJobResponse(id=uuid.UUID(episode_id), status="queued", job_id=job_id)


# ---------------------------------------------------------------------------
# Manual cluster run (QA/debug)
# ---------------------------------------------------------------------------


@router.post("/ideas/generate", response_model=GenerateIdeasResponse, status_code=202)
async def generate_ideas(user_id: str = Depends(current_user_id)) -> GenerateIdeasResponse:
    job_id = await enqueue(
        type="generate_ideas",
        payload={"user_id": user_id},
        user_id=user_id,
    )
    return GenerateIdeasResponse(job_id=job_id, status="queued")
