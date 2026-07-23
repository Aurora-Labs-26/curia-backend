"""
brief/audio.py
Real audio for the daily brief — the piece the harness never had (its
mp3_urls were `placeholder://` stubs). Pure reuse of host primitives:
resolve.tts(speaker) adapters, pydub stitching, core/storage/blob upload.

render_brief_audio(brief_id): read the ready brief's ordered segments
(intro → lead → standards → local → outro), synthesize each with the brief
speaker, stitch with short pauses, upload one MP3 to S3, store the key on
harness.daily_briefs.stitched_mp3_url. Failures never un-ready the brief —
the text manifest remains the product; audio is the enhancement (same
posture as the episode BGM layer).
"""

from __future__ import annotations

import asyncio
import os
import tempfile

from loguru import logger

from brief import store

BRIEF_SPEAKER = os.getenv("CURIA_BRIEF_SPEAKER", "kenji")
SEGMENT_PAUSE_MS = 600


def _stitch(paths: list[str], out_path: str) -> float:
    """Concat WAV/MP3 segment files with pauses → one MP3. Returns seconds."""
    from pydub import AudioSegment

    combined = AudioSegment.silent(duration=300)
    pause = AudioSegment.silent(duration=SEGMENT_PAUSE_MS)
    for p in paths:
        combined += AudioSegment.from_file(p) + pause
    combined.export(out_path, format="mp3", bitrate=os.getenv("CURIA_AUDIO_BITRATE", "128k"))
    return combined.duration_seconds


async def render_brief_audio(brief_id: str) -> str | None:
    """Synthesize + stitch + upload. Returns the S3 key, or None on failure."""
    detail = await store.get_daily_brief_detail(brief_id)
    if not detail or detail.get("status") != "ready":
        logger.info(f"[brief.audio] brief {brief_id} not ready — skipping audio")
        return None
    manifest = await store.get_latest_manifest(detail["user_id"], str(detail["date"])) or []
    texts = [s.get("text", "").strip() for s in manifest if s.get("text", "").strip()]
    if not texts:
        logger.warning(f"[brief.audio] brief {brief_id} has no segment text")
        return None

    from core.llm_config import resolve
    adapter = resolve.tts(speaker=BRIEF_SPEAKER)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            seg_paths = []
            for i, text in enumerate(texts):
                p = os.path.join(tmp, f"seg_{i}.{adapter.output_format}")
                await adapter.synthesize_async(text, p)
                seg_paths.append(p)
            out = os.path.join(tmp, "brief.mp3")
            secs = await asyncio.to_thread(_stitch, seg_paths, out)

            key = f"audio/brief/{brief_id}.mp3"
            from core.storage.blob import upload_file
            await upload_file(out, key, content_type="audio/mpeg")

        await store.set_daily_brief_audio(brief_id, key)
        logger.info(f"[brief.audio] brief {brief_id}: {len(texts)} segments, {secs:.0f}s → {key}")
        return key
    except Exception as e:
        logger.warning(f"[brief.audio] render failed for {brief_id}: {e}")
        return None
