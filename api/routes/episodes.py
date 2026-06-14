"""
api/routes/episodes.py
POST /episodes — create episode row + enqueue generation job.
GET /episodes, GET /episodes/:id, GET /episodes/:id/audio.
"""

import asyncio
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.auth import current_user_id
from api.schemas import (
    CreateEpisodeRequest,
    CreateJobResponse,
    EpisodeDetail,
    EpisodeSourceObject,
    EpisodeSummary,
)
from core.db.connection import db_execute, db_fetchrow, db_query
from core.queue import enqueue

router = APIRouter()


@router.post("/episodes", response_model=CreateJobResponse, status_code=202)
async def create_episode(
    req: CreateEpisodeRequest, user_id: str = Depends(current_user_id)
) -> CreateJobResponse:
    """
    Create a `queued` episode row + enqueue 'generate_episode' job.
    Worker fills in title, outline, transcript, audio_path on completion.
    """
    episode_id = str(uuid.uuid4())
    await db_execute(
        """
        INSERT INTO episode
            (id, user_id, show_name, show_idea_id, editorial_direction,
             length_minutes, speaker_override, speaker_pair, status)
        VALUES
            ($id::uuid, $user_id, $show, $idea_id::uuid, $direction,
             $length_minutes, $speaker_override, $speaker_pair, 'queued')
        """,
        {
            "id": episode_id,
            "user_id": user_id,
            "show": req.show_name,
            "idea_id": str(req.show_idea_id) if req.show_idea_id else None,
            "direction": req.editorial_direction or "",
            "length_minutes": req.length_minutes,
            "speaker_override": req.speaker,
            "speaker_pair": req.speaker_pair if not req.speaker else None,
        },
    )
    job_id = await enqueue(
        type="generate_episode",
        payload={"episode_id": episode_id, "user_id": user_id},
        user_id=user_id,
    )
    return CreateJobResponse(id=uuid.UUID(episode_id), status="queued", job_id=job_id)


@router.get("/episodes", response_model=list[EpisodeSummary])
async def list_episodes(
    user_id: str = Depends(current_user_id),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, le=500),
) -> list[EpisodeSummary]:
    if status:
        rows = await db_query(
            """
            SELECT id, show_name, title, status, created_at, error, quality_score,
                   length_minutes, speaker_override, source_ids, outline,
                   play_progress, listened, last_played_at, show_idea_id,
                   duration_seconds, duration_estimate_seconds, description,
                   chapters, play_position_seconds, failed_stage
            FROM episode
            WHERE user_id = $user_id AND status = $status
            ORDER BY created_at DESC LIMIT $limit
            """,
            {"user_id": user_id, "status": status, "limit": limit},
        )
    else:
        rows = await db_query(
            """
            SELECT id, show_name, title, status, created_at, error, quality_score,
                   length_minutes, speaker_override, source_ids, outline,
                   play_progress, listened, last_played_at, show_idea_id,
                   duration_seconds, duration_estimate_seconds, description,
                   chapters, play_position_seconds, failed_stage
            FROM episode
            WHERE user_id = $user_id
            ORDER BY created_at DESC LIMIT $limit
            """,
            {"user_id": user_id, "limit": limit},
        )

    # Batch-fetch source objects for all episodes in one query
    from urllib.parse import urlparse
    seen: set[str] = set()
    all_source_uuids: list[uuid.UUID] = []
    for r in rows:
        for sid in (r.get("source_ids") or []):
            key = str(sid)
            if key not in seen:
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
            WHERE id = ANY($ids) AND (user_id = $user_id OR (is_seed = true AND user_id = 'seed'))
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
        seen_ids: set[str] = set()
        deduped: list[EpisodeSourceObject] = []
        for sid in (data.get("source_ids") or []):
            key = str(sid)
            if key in source_map and key not in seen_ids:
                seen_ids.add(key)
                deduped.append(source_map[key])
        data["source_objects"] = deduped
        result.append(EpisodeSummary(**data))
    return result


@router.get("/episodes/{episode_id}", response_model=EpisodeDetail)
async def get_episode(
    episode_id: uuid.UUID, user_id: str = Depends(current_user_id)
) -> EpisodeDetail:
    row = await db_fetchrow(
        """
        SELECT id, show_name, title, status, created_at, error,
               transcript, outline, audio_path, source_ids, editorial_direction,
               quality_score, quality_feedback, quality_violations, regenerated,
               length_minutes, speaker_override, tts_timings,
               duration_seconds, duration_estimate_seconds, description,
               chapters, play_progress, play_position_seconds, listened,
               last_played_at, show_idea_id, failed_stage
        FROM episode
        WHERE id = $id::uuid AND (user_id = $user_id OR (is_seed = true AND user_id = 'seed'))
        """,
        {"id": str(episode_id), "user_id": user_id},
    )
    if not row:
        raise HTTPException(404, "episode not found")

    from urllib.parse import urlparse
    data = dict(row)
    source_ids = data.get("source_ids") or []
    source_objects: list[EpisodeSourceObject] = []
    if source_ids:
        source_uuids: list[uuid.UUID] = []
        for sid in source_ids:
            try:
                source_uuids.append(uuid.UUID(str(sid)))
            except (ValueError, TypeError):
                pass
        if source_uuids:
            src_rows = await db_query(
                """
                SELECT id, url, title FROM source
                WHERE id = ANY($ids) AND (user_id = $user_id OR (is_seed = true AND user_id = 'seed'))
                """,
                {"ids": source_uuids, "user_id": user_id},
            )
            for s in src_rows:
                try:
                    domain = urlparse(s["url"]).hostname or s["url"]
                    domain = domain.removeprefix("www.")
                except Exception:
                    domain = s["url"]
                source_objects.append(EpisodeSourceObject(
                    id=s["id"], domain=domain, title=s["title"]
                ))
    data["source_objects"] = source_objects
    return EpisodeDetail(**data)


@router.put("/episodes/{episode_id}/progress", status_code=204)
async def update_episode_progress(
    episode_id: uuid.UUID,
    body: dict,
    user_id: str = Depends(current_user_id),
) -> None:
    play_progress = float(body.get("play_progress") or 0)
    listened = bool(body.get("listened", False))
    # Position in seconds is the durable resume key (STREAMING_PLAN.md):
    # fractions break when duration changes from estimate to exact.
    play_position_seconds = body.get("play_position_seconds")
    if play_position_seconds is not None:
        play_position_seconds = max(0.0, float(play_position_seconds))
    await db_execute(
        """
        UPDATE episode
        SET play_progress = $play_progress,
            play_position_seconds = COALESCE($play_position_seconds, play_position_seconds),
            listened = $listened,
            last_played_at = NOW()
        WHERE id = $id::uuid AND user_id = $user_id
        """,
        {
            "id": str(episode_id),
            "user_id": user_id,
            "play_progress": max(0.0, min(1.0, play_progress)),
            "play_position_seconds": play_position_seconds,
            "listened": listened,
        },
    )


class FeedbackRequest(BaseModel):
    rating: str
    note: str | None = None


@router.post("/episodes/{episode_id}/feedback", status_code=204)
async def submit_episode_feedback(
    episode_id: uuid.UUID,
    body: FeedbackRequest,
    user_id: str = Depends(current_user_id),
) -> None:
    await db_execute(
        """
        UPDATE episode
        SET feedback = $feedback
        WHERE id = $id::uuid AND user_id = $user_id
        """,
        {
            "id": str(episode_id),
            "user_id": user_id,
            "feedback": json.dumps({"rating": body.rating, "note": body.note}),
        },
    )


async def _audio_user_id(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    token: str | None = Query(default=None),
) -> str:
    """Like current_user_id but also accepts ?token= for native audio players that
    cannot set custom headers (e.g. expo-av / ExoPlayer on Android)."""
    from api.auth import _resolve_token
    auth_header = authorization or (f"Bearer {token}" if token else None)
    user = await _resolve_token(auth_header)
    return user.id


def _raw_request_token(request: Request) -> str | None:
    """Extract the raw bearer token so it can be embedded in player-facing URLs
    (native players cannot send Authorization headers — see _audio_user_id)."""
    qt = request.query_params.get("token")
    if qt:
        return qt
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


# Statuses during which the episode is playable via the live HLS stream.
_STREAMABLE_STATUSES = {"script_ready", "synthesizing"}


@router.get("/episodes/{episode_id}/audio")
async def get_episode_audio(
    episode_id: uuid.UUID,
    request: Request,
    user_id: str = Depends(_audio_user_id),
):
    row = await db_fetchrow(
        """
        SELECT audio_path, audio_url, status, title, stream_state, stream_chunks
        FROM episode
        WHERE id = $id::uuid AND (user_id = $user_id OR (is_seed = true AND user_id = 'seed'))
        """,
        {"id": str(episode_id), "user_id": user_id},
    )
    if not row:
        raise HTTPException(404, "episode not found")

    status = row["status"]
    if status != "ready":
        # Streaming path (STREAMING_PLAN.md): the episode is playable from
        # script_ready onward via the growing HLS playlist.
        from fastapi.responses import JSONResponse
        chunks = row.get("stream_chunks")
        if isinstance(chunks, str):
            chunks = json.loads(chunks)
        if status in _STREAMABLE_STATUSES and chunks:
            token = _raw_request_token(request)
            stream_url = str(request.url_for("get_episode_stream_playlist", episode_id=episode_id))
            if token:
                stream_url = f"{stream_url}?token={token}"
            return JSONResponse({"url": stream_url, "streaming": True})
        if status in _STREAMABLE_STATUSES:
            # Script landed but the first audio chunk hasn't been published yet —
            # tell the client to retry shortly (synthesis is eager and fast).
            return JSONResponse({"status": "starting", "retry_in": 2}, status_code=202)
        raise HTTPException(409, f"episode not ready (status={status})")

    # R2 path — return a presigned URL as JSON so the client can stream directly
    audio_url = row.get("audio_url")
    if audio_url:
        from core.storage.blob import generate_presigned_url
        from fastapi.responses import JSONResponse
        presigned = generate_presigned_url(audio_url, expires_in=3600)
        if presigned:
            return JSONResponse({"url": presigned})

    # Local path — stream file with range support
    audio_path = row.get("audio_path")
    if not audio_path or not await asyncio.to_thread(Path(audio_path).exists):
        raise HTTPException(410, "audio file missing on disk")

    file_size = await asyncio.to_thread(lambda: Path(audio_path).stat().st_size)
    filename = f"{row.get('title') or 'episode'}.mp3"
    range_header = request.headers.get("range")

    def _iter_file(path: str, start: int, end: int, chunk: int = 1024 * 64):
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                data = f.read(min(chunk, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    if range_header:
        try:
            range_val = range_header.strip().replace("bytes=", "")
            range_start, range_end = range_val.split("-")
            start = int(range_start)
            end = int(range_end) if range_end else file_size - 1
        except (ValueError, AttributeError):
            raise HTTPException(416, "invalid Range header")

        end = min(end, file_size - 1)
        if start > end or start < 0:
            raise HTTPException(416, "range not satisfiable")

        return StreamingResponse(
            _iter_file(audio_path, start, end),
            status_code=206,
            media_type="audio/mpeg",
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(end - start + 1),
                "Content-Disposition": f'inline; filename="{filename}"',
            },
        )

    return StreamingResponse(
        _iter_file(audio_path, 0, file_size - 1),
        status_code=200,
        media_type="audio/mpeg",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Content-Disposition": f'inline; filename="{filename}"',
        },
    )


# ---------------------------------------------------------------------------
# Live HLS streaming (STREAMING_PLAN.md)
# ---------------------------------------------------------------------------


@router.get("/episodes/{episode_id}/stream.m3u8", name="get_episode_stream_playlist")
async def get_episode_stream_playlist(
    episode_id: uuid.UUID,
    request: Request,
    user_id: str = Depends(_audio_user_id),
):
    """HLS event playlist for an episode whose audio is (or was) streamed live.

    Grows as chunks are published during synthesis; gains #EXT-X-ENDLIST when
    the stream ends and becomes a normal VOD. Segment URLs are presigned R2
    URLs (S3 backend) or API chunk URLs (local backend) — regenerated on every
    fetch, which sidesteps both presign expiry and player auth headers.
    """
    row = await db_fetchrow(
        """
        SELECT status, stream_state, stream_chunks FROM episode
        WHERE id = $id::uuid AND (user_id = $user_id OR (is_seed = true AND user_id = 'seed'))
        """,
        {"id": str(episode_id), "user_id": user_id},
    )
    if not row:
        raise HTTPException(404, "episode not found")

    chunks = row.get("stream_chunks")
    if isinstance(chunks, str):
        chunks = json.loads(chunks)
    if not chunks:
        raise HTTPException(409, "no stream available for this episode")

    from core.storage.blob import generate_presigned_url, get_storage_backend

    token = _raw_request_token(request)
    use_presigned = get_storage_backend() == "s3"

    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-PLAYLIST-TYPE:EVENT",
        f"#EXT-X-TARGETDURATION:{max(1, max(int(-(-c['duration_ms'] // 1000)) for c in chunks))}",
        "#EXT-X-MEDIA-SEQUENCE:0",
    ]
    for c in chunks:
        url: str | None = None
        if use_presigned:
            url = generate_presigned_url(c["key"], expires_in=3600)
        if not url:
            name = c["key"].rsplit("/", 1)[-1]
            url = str(request.url_for(
                "get_episode_stream_chunk", episode_id=episode_id, chunk_name=name
            ))
            if token:
                url = f"{url}?token={token}"
        lines.append(f"#EXTINF:{c['duration_ms'] / 1000:.3f},")
        lines.append(url)

    ended = row.get("stream_state") == "ended"
    if ended:
        lines.append("#EXT-X-ENDLIST")

    from fastapi.responses import Response
    return Response(
        content="\n".join(lines) + "\n",
        media_type="application/vnd.apple.mpegurl",
        headers={
            # The growing playlist must never be cached; once ended, brief caching is fine.
            "Cache-Control": "public, max-age=60" if ended else "no-store",
        },
    )


@router.get("/episodes/{episode_id}/stream/chunks/{chunk_name}", name="get_episode_stream_chunk")
async def get_episode_stream_chunk(
    episode_id: uuid.UUID,
    chunk_name: str,
    user_id: str = Depends(_audio_user_id),
):
    """Serve a stream chunk from local blob storage (dev backend only — the S3
    backend serves chunks directly from R2 via presigned URLs)."""
    import os
    import re

    if not re.fullmatch(r"seg_\d{4}\.aac", chunk_name):
        raise HTTPException(404, "no such chunk")

    # Ownership check
    row = await db_fetchrow(
        """
        SELECT id FROM episode
        WHERE id = $id::uuid AND (user_id = $user_id OR (is_seed = true AND user_id = 'seed'))
        """,
        {"id": str(episode_id), "user_id": user_id},
    )
    if not row:
        raise HTTPException(404, "episode not found")

    base = Path(os.getenv("CURIA_STORAGE_LOCAL_DIR", "data/blobs"))
    chunk_path = base / "audio" / str(episode_id) / "live" / chunk_name
    if not await asyncio.to_thread(chunk_path.exists):
        raise HTTPException(404, "chunk not found")

    data = await asyncio.to_thread(chunk_path.read_bytes)
    from fastapi.responses import Response
    return Response(
        content=data,
        media_type="audio/aac",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )
