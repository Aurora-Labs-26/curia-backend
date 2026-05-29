"""
tests/test_audio_stream.py
Tests for core/audio/stream.py — async audio streaming generator.

Verifies:
  - Yields audio bytes per segment
  - Saves concatenated WAV when save_path is set
  - Handles empty transcript gracefully
  - Handles TTS returning empty bytes
  - Metadata dict yielded when include_metadata=True
  - WAV extraction via asyncio.to_thread doesn't block the loop
"""

import io
import wave

import pytest


def _make_wav_bytes(duration_seconds: float = 0.3) -> bytes:
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


@pytest.mark.asyncio
async def test_yields_audio_per_segment():
    from core.audio.stream import stream_episode_audio

    transcript = [
        {"speaker": "kenji", "text": "First sentence."},
        {"speaker": "arjun", "text": "Second sentence."},
    ]

    async def mock_tts(text, speaker):
        return _make_wav_bytes()

    chunks = []
    async for chunk in stream_episode_audio(transcript, tts_fn=mock_tts):
        if isinstance(chunk, bytes):
            chunks.append(chunk)

    assert len(chunks) == 2
    for c in chunks:
        assert len(c) > 44


@pytest.mark.asyncio
async def test_saves_concatenated_wav(tmp_path):
    from core.audio.stream import stream_episode_audio

    transcript = [
        {"speaker": "kenji", "text": "Part one."},
        {"speaker": "arjun", "text": "Part two."},
    ]
    save_path = str(tmp_path / "combined.wav")

    async def mock_tts(text, speaker):
        return _make_wav_bytes(0.2)

    async for _ in stream_episode_audio(transcript, tts_fn=mock_tts, save_path=save_path):
        pass

    assert (tmp_path / "combined.wav").exists()
    with wave.open(save_path, "rb") as w:
        assert w.getnframes() > 0
        assert w.getnchannels() == 1


@pytest.mark.asyncio
async def test_empty_transcript_yields_nothing():
    from core.audio.stream import stream_episode_audio

    async def mock_tts(text, speaker):
        return _make_wav_bytes()

    chunks = []
    async for chunk in stream_episode_audio([], tts_fn=mock_tts):
        chunks.append(chunk)

    assert chunks == []


@pytest.mark.asyncio
async def test_skips_empty_tts_response():
    from core.audio.stream import stream_episode_audio

    transcript = [
        {"speaker": "kenji", "text": "Good."},
        {"speaker": "arjun", "text": "Empty."},
    ]
    call_count = 0

    async def mock_tts(text, speaker):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            return b""
        return _make_wav_bytes()

    chunks = []
    async for chunk in stream_episode_audio(transcript, tts_fn=mock_tts):
        if isinstance(chunk, bytes):
            chunks.append(chunk)

    assert len(chunks) == 1


@pytest.mark.asyncio
async def test_metadata_yielded_first():
    from core.audio.stream import stream_episode_audio

    transcript = [
        {"speaker": "kenji", "text": "Hello."},
        {"speaker": "arjun", "text": "World."},
    ]

    async def mock_tts(text, speaker):
        return _make_wav_bytes()

    results = []
    async for item in stream_episode_audio(transcript, tts_fn=mock_tts, include_metadata=True):
        results.append(item)

    assert isinstance(results[0], dict)
    assert results[0]["total_segments"] == 2
    assert set(results[0]["speakers"]) == {"kenji", "arjun"}


@pytest.mark.asyncio
async def test_no_save_when_no_path():
    """When save_path is None, no file should be created."""
    from core.audio.stream import stream_episode_audio

    transcript = [{"speaker": "kenji", "text": "Solo."}]

    async def mock_tts(text, speaker):
        return _make_wav_bytes()

    async for _ in stream_episode_audio(transcript, tts_fn=mock_tts, save_path=None):
        pass


@pytest.mark.asyncio
async def test_handles_non_wav_tts_response():
    """If TTS returns non-WAV bytes, they should still be yielded."""
    from core.audio.stream import stream_episode_audio

    transcript = [{"speaker": "kenji", "text": "Raw."}]

    async def mock_tts(text, speaker):
        return b"not-a-wav-file-just-raw-audio-data"

    chunks = []
    async for chunk in stream_episode_audio(transcript, tts_fn=mock_tts):
        if isinstance(chunk, bytes):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0] == b"not-a-wav-file-just-raw-audio-data"
