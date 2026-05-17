"""
api/routes/stream.py
WebSocket endpoint for real-time audio streaming.

Client connects → server streams audio segment-by-segment → client plays in real-time.
Also saves the full audio to disk so subsequent plays use the file (no re-generation).
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from core.audio.stream_manager import StreamManager
from core.db.connection import db_fetchrow
from core.tts import synthesize_for_speaker_bytes

router = APIRouter()

EPISODES_DIR = Path(os.getenv("CURIA_AUDIO_DIR", str(Path(__file__).parent.parent.parent / "data" / "audio")))


@router.websocket("/ws/episodes/{episode_id}/stream")
async def stream_episode(websocket: WebSocket, episode_id: str):
    """
    WebSocket endpoint for real-time episode audio streaming.

    Protocol:
      1. Client connects with auth token as query param: ?token=ck_...
      2. Server sends JSON metadata, then binary audio per segment, then JSON completion
      3. Audio is also saved to disk for replay via GET /episodes/{id}/audio
    """
    # Auth via query param (WebSockets can't use headers easily)
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Missing token query parameter")
        return

    # Validate token
    user_row = await db_fetchrow(
        "SELECT id, role FROM users WHERE api_token = $token",
        {"token": token},
    )
    if not user_row:
        await websocket.close(code=4003, reason="Invalid token")
        return

    user_id = str(user_row["id"])

    # Fetch episode
    episode = await db_fetchrow(
        "SELECT id, transcript, status, audio_path FROM episode WHERE id = $id::uuid AND user_id = $uid",
        {"id": episode_id, "uid": user_id},
    )
    if not episode:
        await websocket.close(code=4004, reason="Episode not found")
        return

    if episode["status"] != "ready" or not episode.get("transcript"):
        await websocket.close(code=4009, reason=f"Episode not ready (status={episode['status']})")
        return

    await websocket.accept()

    transcript = episode["transcript"]
    if isinstance(transcript, str):
        import json
        transcript = json.loads(transcript)

    # Save path for replay
    save_path = str(EPISODES_DIR / f"{episode_id}_stream.wav")
    EPISODES_DIR.mkdir(parents=True, exist_ok=True)

    try:
        mgr = StreamManager(tts_fn=synthesize_for_speaker_bytes, save_path=save_path)
        await mgr.stream_to_websocket(transcript, websocket)
    except WebSocketDisconnect:
        logger.info(f"Client disconnected during stream: episode={episode_id}")
    except Exception as e:
        logger.error(f"Stream error for episode={episode_id}: {e}")
        try:
            await websocket.close(code=1011, reason=str(e)[:120])
        except Exception:
            pass
