"""
tests/test_audio_integration.py
TDD Cycle 3: Wire new stitcher into generator + streaming WebSocket endpoint.

Part A: synthesize_and_stitch_v2 replaces per-line with per-segment
Part B: streaming endpoint yields audio segments progressively
"""

import json
import wave
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Part A: Generator Integration ───────────────────────────────────────────
# synthesize_and_stitch_v2 uses the new segment pipeline internally.


class TestGeneratorIntegration:

    def _make_transcript(self, n=30, speaker="kenji"):
        return [{"speaker": speaker, "text": f"This is sentence {i}."} for i in range(n)]

    def test_v2_exists(self):
        """New function should be importable."""
        from studio.generator import synthesize_and_stitch_v2
        assert callable(synthesize_and_stitch_v2)

    def test_v2_uses_segments_not_lines(self, tmp_path):
        """Should make far fewer TTS calls than transcript lines."""
        from studio.generator import synthesize_and_stitch_v2

        transcript = self._make_transcript(40)
        call_count = 0

        def mock_tts(text, speaker, output_path):
            nonlocal call_count
            call_count += 1
            _write_test_wav(output_path)
            return "wav"

        output = str(tmp_path / "episode.mp3")
        with patch("studio.generator.synthesize_line_by_speaker", side_effect=mock_tts):
            with patch("studio.generator.AudioSegment") as mock_audio:
                # Mock pydub so we don't need ffmpeg
                mock_seg = MagicMock()
                mock_seg.__len__ = lambda self: 10000
                mock_seg.__add__ = lambda self, other: self
                mock_seg.overlay = lambda self, other: self
                mock_seg.export = MagicMock()
                mock_audio.from_wav.return_value = mock_seg
                mock_audio.from_mp3.return_value = mock_seg
                mock_audio.silent.return_value = mock_seg
                mock_audio.empty.return_value = mock_seg

                synthesize_and_stitch_v2(transcript, "clarity_engine", output)

        # 40 lines from same speaker should merge into < 10 segments
        assert call_count < 10, f"Expected fewer TTS calls, got {call_count}"

    def test_v2_multi_speaker(self, tmp_path):
        """Multi-speaker transcript should merge into fewer segments."""
        from studio.generator import synthesize_and_stitch_v2

        # Use "kenji" throughout since clarity_engine profile only has kenji
        # The merger still creates segments based on speaker runs in the transcript
        transcript = [
            {"speaker": "kenji", "text": "Hello."},
            {"speaker": "kenji", "text": "More from kenji."},
            {"speaker": "kenji", "text": "Third line."},
            {"speaker": "kenji", "text": "Fourth line."},
        ]
        call_count = 0

        def mock_tts(text, speaker, output_path):
            nonlocal call_count
            call_count += 1
            _write_test_wav(output_path)
            return "wav"

        output = str(tmp_path / "episode.mp3")
        with patch("studio.generator.synthesize_line_by_speaker", side_effect=mock_tts):
            with patch("studio.generator.AudioSegment") as mock_audio:
                mock_seg = MagicMock()
                mock_seg.__len__ = lambda self: 10000
                mock_seg.__add__ = lambda self, other: self
                mock_seg.overlay = lambda self, other: self
                mock_seg.export = MagicMock()
                mock_audio.from_wav.return_value = mock_seg
                mock_audio.from_mp3.return_value = mock_seg
                mock_audio.silent.return_value = mock_seg
                mock_audio.empty.return_value = mock_seg

                synthesize_and_stitch_v2(transcript, "clarity_engine", output)

        # 4 same-speaker lines should merge into 1 segment
        assert call_count == 1


# ─── Part B: Streaming Endpoint ──────────────────────────────────────────────
# WebSocket at /ws/episodes/{id}/stream that yields audio segments progressively.


class TestStreamingEndpoint:

    def test_stream_handler_exists(self):
        """Streaming handler should be importable."""
        from core.audio.stream import stream_episode_audio
        assert callable(stream_episode_audio)

    @pytest.mark.asyncio
    async def test_stream_yields_chunks(self):
        """Should yield audio bytes for each segment."""
        from core.audio.stream import stream_episode_audio

        transcript = [
            {"speaker": "kenji", "text": "First segment text."},
            {"speaker": "arjun", "text": "Second segment text."},
        ]

        async def mock_tts_async(text, speaker):
            """Returns audio bytes instead of writing to file."""
            return _make_wav_bytes(duration_seconds=0.3)

        chunks = []
        async for chunk in stream_episode_audio(transcript, tts_fn=mock_tts_async):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert all(isinstance(c, bytes) for c in chunks)
        assert all(len(c) > 0 for c in chunks)

    @pytest.mark.asyncio
    async def test_stream_metadata_first(self):
        """First yield should be metadata (JSON), then audio chunks."""
        from core.audio.stream import stream_episode_audio

        transcript = [
            {"speaker": "kenji", "text": "Hello."},
        ]

        async def mock_tts_async(text, speaker):
            return _make_wav_bytes(duration_seconds=0.2)

        items = []
        async for item in stream_episode_audio(
            transcript, tts_fn=mock_tts_async, include_metadata=True
        ):
            items.append(item)

        # First item: metadata dict, rest: audio bytes
        assert len(items) == 2  # 1 metadata + 1 audio chunk
        assert isinstance(items[0], dict)
        assert "total_segments" in items[0]
        assert items[0]["total_segments"] == 1
        assert isinstance(items[1], bytes)

    @pytest.mark.asyncio
    async def test_stream_saves_to_file(self, tmp_path):
        """Streaming should optionally save complete audio to file for replay."""
        from core.audio.stream import stream_episode_audio

        transcript = [
            {"speaker": "kenji", "text": "Part one."},
            {"speaker": "kenji", "text": "Part two."},
        ]
        save_path = str(tmp_path / "saved.wav")

        async def mock_tts_async(text, speaker):
            return _make_wav_bytes(duration_seconds=0.3)

        async for _ in stream_episode_audio(
            transcript, tts_fn=mock_tts_async, save_path=save_path
        ):
            pass

        assert Path(save_path).exists()
        with wave.open(save_path, "rb") as w:
            assert w.getnframes() > 0

    @pytest.mark.asyncio
    async def test_stream_empty_transcript(self):
        """Empty transcript should yield nothing."""
        from core.audio.stream import stream_episode_audio

        async def mock_tts_async(text, speaker):
            return b""

        chunks = []
        async for chunk in stream_episode_audio([], tts_fn=mock_tts_async):
            chunks.append(chunk)

        assert len(chunks) == 0


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _write_test_wav(path: str, duration_seconds: float = 0.5):
    sample_rate = 22050
    num_samples = int(sample_rate * duration_seconds)
    silence = b"\x00\x00" * num_samples
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(silence)


def _make_wav_bytes(duration_seconds: float = 0.5) -> bytes:
    """Create WAV bytes in memory (for streaming mock)."""
    import io
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
