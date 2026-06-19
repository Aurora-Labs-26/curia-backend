"""
tests/test_tts_facade.py
Tests for core/tts.py — TTS facade routing to adapters via resolver.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.tts as tts


class TestSynthesizeForSpeaker:
    def test_calls_resolve_and_synthesize(self):
        mock_adapter = MagicMock()
        mock_adapter.output_format = "wav"
        with patch("core.tts.resolve") as mock_resolve:
            mock_resolve.tts.return_value = mock_adapter
            fmt = tts.synthesize_for_speaker("Hello", "kenji", "/tmp/out.wav")
        assert fmt == "wav"
        mock_resolve.tts.assert_called_once_with(speaker="kenji")
        mock_adapter.synthesize.assert_called_once_with(text="Hello", output_path="/tmp/out.wav")

    def test_returns_mp3_format(self):
        mock_adapter = MagicMock()
        mock_adapter.output_format = "mp3"
        with patch("core.tts.resolve") as mock_resolve:
            mock_resolve.tts.return_value = mock_adapter
            fmt = tts.synthesize_for_speaker("Hi", "arjun", "/tmp/out.mp3")
        assert fmt == "mp3"


class TestSynthesizeForSpeakerWithTimings:
    def test_returns_format_and_timings(self):
        mock_adapter = MagicMock()
        mock_adapter.output_format = "wav"
        mock_adapter.synthesize_with_timings.return_value = [{"word": "Hello", "start": 0.0}]
        with patch("core.tts.resolve") as mock_resolve:
            mock_resolve.tts.return_value = mock_adapter
            fmt, timings = tts.synthesize_for_speaker_with_timings("Hello", "kenji", "/tmp/out.wav")
        assert fmt == "wav"
        assert len(timings) == 1

    def test_empty_timings(self):
        mock_adapter = MagicMock()
        mock_adapter.output_format = "wav"
        mock_adapter.synthesize_with_timings.return_value = []
        with patch("core.tts.resolve") as mock_resolve:
            mock_resolve.tts.return_value = mock_adapter
            _, timings = tts.synthesize_for_speaker_with_timings("Hello", "kenji", "/tmp/out.wav")
        assert timings == []


class TestSynthesizeForSpeakerAsync:
    async def test_async_calls_adapter(self):
        mock_adapter = MagicMock()
        mock_adapter.output_format = "wav"
        mock_adapter.synthesize_async = AsyncMock()
        with patch("core.tts.resolve") as mock_resolve:
            mock_resolve.tts.return_value = mock_adapter
            fmt = await tts.synthesize_for_speaker_async("Hello", "kenji", "/tmp/out.wav")
        assert fmt == "wav"
        mock_adapter.synthesize_async.assert_awaited_once()


class TestSynthesizeForSpeakerBytes:
    async def test_returns_bytes(self):
        mock_adapter = MagicMock()
        mock_adapter.synthesize_bytes = AsyncMock(return_value=b"\x00\x01\x02")
        with patch("core.tts.resolve") as mock_resolve:
            mock_resolve.tts.return_value = mock_adapter
            data = await tts.synthesize_for_speaker_bytes("Hello", "kenji")
        assert data == b"\x00\x01\x02"


class TestSynthesizeLine:
    def test_legacy_entrypoint(self):
        mock_config = MagicMock()
        mock_model = MagicMock()
        mock_model.kind = "tts"
        mock_model.provider = "edge"
        mock_model.defaults = {}
        mock_provider = MagicMock()
        mock_config.models = {"edge-tts": mock_model}
        mock_config.providers = {"edge": mock_provider}

        mock_adapter = MagicMock()

        with patch("core.tts.get_config", return_value=mock_config), \
             patch("core.tts.build_tts", return_value=mock_adapter):
            tts.synthesize_line("Hello", "voice-id-123", "/tmp/out.wav")

        mock_adapter.synthesize.assert_called_once_with(text="Hello", output_path="/tmp/out.wav")

    def test_no_tts_model_raises(self):
        mock_config = MagicMock()
        llm_model = MagicMock()
        llm_model.kind = "llm"
        mock_config.models = {"sonnet": llm_model}

        with patch("core.tts.get_config", return_value=mock_config):
            with pytest.raises(RuntimeError, match="No TTS-kind model"):
                tts.synthesize_line("Hello", "voice-id", "/tmp/out.wav")
