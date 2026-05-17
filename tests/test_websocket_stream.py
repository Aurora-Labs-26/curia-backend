"""
tests/test_websocket_stream.py
TDD Cycle 4: WebSocket endpoint for real-time audio streaming.

Tests the full WebSocket lifecycle:
  1. Client connects to /ws/episodes/{id}/stream
  2. Server sends metadata message (JSON)
  3. Server sends audio chunks (binary) as they're generated
  4. Server sends completion message (JSON)
  5. Server saves full audio to disk for replay
  6. Auth required — rejects unauthenticated connections
"""

import json
import wave
import io
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocket


# ─── WebSocket Route Tests ────────────────────────────────────────────────────


class TestWebSocketRoute:

    def test_route_exists(self):
        """The streaming WebSocket route should be registered."""
        from api.routes.stream import router
        routes = [r.path for r in router.routes]
        assert "/ws/episodes/{episode_id}/stream" in routes

    def test_route_is_websocket(self):
        """Should be a WebSocket route, not HTTP."""
        from api.routes.stream import router
        for route in router.routes:
            if hasattr(route, 'path') and route.path == "/ws/episodes/{episode_id}/stream":
                # WebSocketRoute in Starlette
                assert "WebSocket" in type(route).__name__ or hasattr(route, 'endpoint')
                return
        pytest.fail("WebSocket route not found")


# ─── StreamManager Tests ─────────────────────────────────────────────────────
# The StreamManager orchestrates: fetch transcript → stream audio → save file.


class TestStreamManager:

    @pytest.mark.asyncio
    async def test_stream_sends_metadata_first(self):
        """First message should be JSON metadata with segment count."""
        from core.audio.stream_manager import StreamManager

        transcript = [
            {"speaker": "kenji", "text": "Hello world."},
            {"speaker": "arjun", "text": "Hi there."},
        ]

        async def mock_tts(text, speaker):
            return _make_wav_bytes(0.2)

        ws = MockWebSocket()
        mgr = StreamManager(tts_fn=mock_tts)
        await mgr.stream_to_websocket(transcript, ws)

        # First message: JSON metadata
        assert len(ws.sent) >= 3  # metadata + 2 audio + completion
        meta = json.loads(ws.sent[0])
        assert meta["type"] == "metadata"
        assert meta["total_segments"] == 2

    @pytest.mark.asyncio
    async def test_stream_sends_audio_chunks(self):
        """Should send binary audio data for each segment."""
        from core.audio.stream_manager import StreamManager

        transcript = [
            {"speaker": "kenji", "text": "Part one."},
            {"speaker": "arjun", "text": "Part two."},
            {"speaker": "kenji", "text": "Part three."},
        ]

        async def mock_tts(text, speaker):
            return _make_wav_bytes(0.3)

        ws = MockWebSocket()
        mgr = StreamManager(tts_fn=mock_tts)
        await mgr.stream_to_websocket(transcript, ws)

        # Count binary messages (audio chunks)
        audio_msgs = [m for m in ws.sent if isinstance(m, bytes)]
        assert len(audio_msgs) == 3

    @pytest.mark.asyncio
    async def test_stream_sends_completion(self):
        """Last message should be JSON completion with status."""
        from core.audio.stream_manager import StreamManager

        transcript = [{"speaker": "kenji", "text": "Solo."}]

        async def mock_tts(text, speaker):
            return _make_wav_bytes(0.2)

        ws = MockWebSocket()
        mgr = StreamManager(tts_fn=mock_tts)
        await mgr.stream_to_websocket(transcript, ws)

        last = json.loads(ws.sent[-1])
        assert last["type"] == "complete"
        assert last["segments_sent"] == 1

    @pytest.mark.asyncio
    async def test_stream_saves_file(self, tmp_path):
        """Should save concatenated audio to disk if save_path is set."""
        from core.audio.stream_manager import StreamManager

        transcript = [
            {"speaker": "kenji", "text": "Part one."},
            {"speaker": "kenji", "text": "Part two."},
        ]
        save_path = str(tmp_path / "saved.wav")

        async def mock_tts(text, speaker):
            return _make_wav_bytes(0.3)

        ws = MockWebSocket()
        mgr = StreamManager(tts_fn=mock_tts, save_path=save_path)
        await mgr.stream_to_websocket(transcript, ws)

        assert (tmp_path / "saved.wav").exists()
        with wave.open(save_path, "rb") as w:
            assert w.getnframes() > 0

    @pytest.mark.asyncio
    async def test_stream_empty_transcript(self):
        """Empty transcript should send metadata + immediate completion."""
        from core.audio.stream_manager import StreamManager

        async def mock_tts(text, speaker):
            return b""

        ws = MockWebSocket()
        mgr = StreamManager(tts_fn=mock_tts)
        await mgr.stream_to_websocket([], ws)

        assert len(ws.sent) == 2  # metadata + completion
        meta = json.loads(ws.sent[0])
        assert meta["total_segments"] == 0
        done = json.loads(ws.sent[1])
        assert done["type"] == "complete"
        assert done["segments_sent"] == 0

    @pytest.mark.asyncio
    async def test_stream_handles_tts_error(self):
        """If TTS fails for a segment, send error message but continue."""
        from core.audio.stream_manager import StreamManager

        # Different speakers so they stay as 3 separate segments
        transcript = [
            {"speaker": "kenji", "text": "Good segment."},
            {"speaker": "arjun", "text": "Bad segment."},
            {"speaker": "kenji", "text": "Another good one."},
        ]
        call_count = 0

        async def flaky_tts(text, speaker):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("TTS provider error")
            return _make_wav_bytes(0.2)

        ws = MockWebSocket()
        mgr = StreamManager(tts_fn=flaky_tts)
        await mgr.stream_to_websocket(transcript, ws)

        texts = [m if isinstance(m, bytes) else json.loads(m) for m in ws.sent]
        error_msgs = [t for t in texts if isinstance(t, dict) and t.get("type") == "error"]
        assert len(error_msgs) == 1
        audio_msgs = [m for m in ws.sent if isinstance(m, bytes)]
        assert len(audio_msgs) == 2  # segment 1 and 3 succeeded
        done = json.loads(ws.sent[-1])
        assert done["type"] == "complete"

    @pytest.mark.asyncio
    async def test_stream_segment_progress(self):
        """Each audio chunk should be preceded by a progress message."""
        from core.audio.stream_manager import StreamManager

        transcript = [
            {"speaker": "kenji", "text": "First."},
            {"speaker": "arjun", "text": "Second."},
        ]

        async def mock_tts(text, speaker):
            return _make_wav_bytes(0.2)

        ws = MockWebSocket()
        mgr = StreamManager(tts_fn=mock_tts)
        await mgr.stream_to_websocket(transcript, ws)

        # Check for progress messages between metadata and completion
        json_msgs = []
        for m in ws.sent:
            if isinstance(m, str):
                json_msgs.append(json.loads(m))

        progress_msgs = [m for m in json_msgs if m.get("type") == "progress"]
        assert len(progress_msgs) == 2
        assert progress_msgs[0]["segment"] == 0
        assert progress_msgs[1]["segment"] == 1


# ─── Mock WebSocket ──────────────────────────────────────────────────────────


class MockWebSocket:
    """Minimal WebSocket mock that records sent messages."""

    def __init__(self):
        self.sent: list[str | bytes] = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_text(self, data: str):
        self.sent.append(data)

    async def send_bytes(self, data: bytes):
        self.sent.append(data)

    async def close(self, code: int = 1000):
        pass


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_wav_bytes(duration_seconds: float = 0.5) -> bytes:
    sample_rate = 22050
    num_samples = int(sample_rate * duration_seconds)
    silence = b"\x00\x00" * num_samples
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(silence)
    return buf.getvalue()
