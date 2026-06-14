"""
core/audio/hls.py
Incremental HLS publishing for streaming playback (STREAMING_PLAN.md).

HlsPublisher is handed to the synchronous synthesis loop (which runs in a
thread executor) as an on_segment callback. After each segment WAV is
synthesized it:
  1. splits the clip (+ inter-segment gap) into short HLS-sized parts and
     transcodes each part to AAC/ADTS,
  2. uploads it to blob storage under audio/{episode_id}/live/seg_NNNN.aac,
  3. appends {key, duration_ms} to episode.stream_chunks and, on the first
     chunk, sets stream_state='live' so the playlist endpoint goes live.

Publishing is best-effort: a failed chunk upload degrades the live stream but
never fails episode generation — the stitched MP3 path is the source of truth.

The publisher is constructed on the event loop thread and called from the
executor thread; async work is marshalled back via run_coroutine_threadsafe.
"""

from __future__ import annotations

import asyncio
import io
import json
import os

from loguru import logger
from pydub import AudioSegment

PUBLISH_RETRIES = 2
DEFAULT_TARGET_CHUNK_MS = 8000


def stream_chunk_key(episode_id: str, index: int) -> str:
    return f"audio/{episode_id}/live/seg_{index:04d}.aac"


class HlsPublisher:
    """Publishes synthesized segments as HLS chunks while generation runs."""

    def __init__(
        self,
        episode_id: str,
        loop: asyncio.AbstractEventLoop,
        gap_ms: int = 400,
        target_chunk_ms: int | None = None,
    ):
        self.episode_id = episode_id
        self._loop = loop
        self.gap_ms = gap_ms
        self.target_chunk_ms = target_chunk_ms or int(
            os.getenv("CURIA_HLS_CHUNK_TARGET_MS", str(DEFAULT_TARGET_CHUNK_MS))
        )
        self._next_index = 0
        self._first_chunk_published = False
        self._disabled = False
        self.published: list[dict] = []  # [{key, duration_ms}]

    # -- called from the synthesis (executor) thread -------------------------

    def publish_intro(self, intro_clip: AudioSegment) -> None:
        """Publish the intro as chunk 0 so the stream timeline matches the
        final stitched MP3 (chapters are intro-offset)."""
        self._publish_clip(intro_clip, label="intro")

    def publish_segment(self, index: int, clip: AudioSegment) -> None:
        """Publish one synthesized segment followed by the stitch gap, mirroring
        how synthesize_and_stitch_v2 lays out the final file."""
        clip_with_gap = clip + AudioSegment.silent(duration=self.gap_ms)
        self._publish_clip_chunked(clip_with_gap, label=f"segment {index}")

    def finalize(self) -> None:
        """Mark the stream ended (playlist gains #EXT-X-ENDLIST)."""
        if self._disabled and not self.published:
            return
        self._run(self._db_set_stream_state("ended"))

    # -- internals ------------------------------------------------------------

    def _publish_clip_chunked(self, clip: AudioSegment, label: str) -> None:
        """Emit short HLS parts even when a TTS segment is a long paragraph."""
        target = max(1000, int(self.target_chunk_ms))
        if len(clip) <= target:
            self._publish_clip(clip, label=label)
            return

        part = 0
        for start_ms in range(0, len(clip), target):
            if self._disabled:
                return
            chunk = clip[start_ms: start_ms + target]
            if len(chunk) <= 0:
                continue
            self._publish_clip(chunk, label=f"{label} part {part}")
            part += 1

    def _publish_clip(self, clip: AudioSegment, label: str) -> None:
        if self._disabled:
            return
        index = self._next_index
        key = stream_chunk_key(self.episode_id, index)
        duration_ms = len(clip)

        try:
            buf = io.BytesIO()
            clip.export(buf, format="adts")  # AAC in ADTS framing — HLS-native
            data = buf.getvalue()
        except Exception as e:
            logger.warning(f"[hls] transcode failed for {label} ({self.episode_id}): {e} — disabling stream")
            self._disabled = True
            return

        for attempt in range(1 + PUBLISH_RETRIES):
            try:
                self._run(self._upload_and_record(key, data, duration_ms))
                self._next_index += 1
                self.published.append({"key": key, "duration_ms": duration_ms})
                logger.info(f"[hls] published {key} ({duration_ms} ms)")
                return
            except Exception as e:
                if attempt < PUBLISH_RETRIES:
                    logger.warning(f"[hls] publish attempt {attempt + 1} failed for {key}: {e} — retrying")
                else:
                    logger.warning(f"[hls] giving up on {key}: {e} — disabling stream for this episode")
                    self._disabled = True

    def _run(self, coro):
        """Run a coroutine on the main event loop and wait for it (we are on the
        executor thread; blocking here just paces synthesis, which is fine)."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=120)

    async def _upload_and_record(self, key: str, data: bytes, duration_ms: int) -> None:
        from core.storage.blob import upload_blob

        await upload_blob(data=data, key=key, content_type="audio/aac")

        chunk = {"key": key, "duration_ms": duration_ms}
        if not self._first_chunk_published:
            # First chunk: stream goes live and the episode becomes audibly playable.
            from core.db.connection import db_execute
            await db_execute(
                """
                UPDATE episode
                SET stream_chunks = COALESCE(stream_chunks, '[]'::jsonb) || $chunk::jsonb,
                    stream_state = 'live',
                    status = 'synthesizing'
                WHERE id = $id::uuid
                """,
                {"id": self.episode_id, "chunk": json.dumps([chunk])},
            )
            self._first_chunk_published = True
        else:
            from core.db.connection import db_execute
            await db_execute(
                """
                UPDATE episode
                SET stream_chunks = COALESCE(stream_chunks, '[]'::jsonb) || $chunk::jsonb
                WHERE id = $id::uuid
                """,
                {"id": self.episode_id, "chunk": json.dumps([chunk])},
            )

    async def _db_set_stream_state(self, state: str) -> None:
        from core.db.connection import db_execute
        await db_execute(
            "UPDATE episode SET stream_state = $state WHERE id = $id::uuid",
            {"id": self.episode_id, "state": state},
        )
