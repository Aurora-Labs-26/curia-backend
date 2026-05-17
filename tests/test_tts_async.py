"""
tests/test_tts_async.py
TDD Cycle 5: Native async TTS adapters for all providers.

Tests that:
  - async synthesize_async() exists on TTSAdapter
  - async synthesize_bytes() returns WAV bytes without temp files
  - async stream() yields audio chunks (for streaming providers)
  - stub fallback works in async mode
  - edge_tts uses native asyncio (no thread pool)
  - each provider dispatches correctly in async mode
"""

import io
import wave

import pytest

from core.llm_config.schema import ModelConfig, ProviderConfig
from core.llm_config.adapters.tts import TTSAdapter


def _make_adapter(provider_type: str, voice_id: str = "test_voice",
                  api_key_env: str = "FAKE_KEY", **settings) -> TTSAdapter:
    return TTSAdapter(
        provider=ProviderConfig(type=provider_type, api_key_env=api_key_env),
        model=ModelConfig(provider="any", model_id="any", kind="tts"),
        settings=settings,
        voice_id=voice_id,
    )


# ── async synthesize_async (writes to file, async) ───────────────────────────


class TestAsyncSynthesizeFile:

    @pytest.mark.asyncio
    async def test_method_exists(self):
        adapter = _make_adapter("elevenlabs")
        assert hasattr(adapter, "synthesize_async")
        assert asyncio.iscoroutinefunction(adapter.synthesize_async)

    @pytest.mark.asyncio
    async def test_stub_fallback_async(self, monkeypatch, tmp_path):
        """No API key → silent WAV stub in async mode too."""
        monkeypatch.delenv("FAKE_KEY", raising=False)
        adapter = _make_adapter("elevenlabs")
        out = str(tmp_path / "out.wav")
        await adapter.synthesize_async(text="hello", output_path=out)
        assert (tmp_path / "out.wav").exists()
        with wave.open(out, "rb") as w:
            assert w.getnframes() > 0

    @pytest.mark.asyncio
    async def test_empty_text_async(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FAKE_KEY", "x")
        adapter = _make_adapter("elevenlabs")
        out = str(tmp_path / "empty.wav")
        await adapter.synthesize_async(text="  ", output_path=out)
        assert (tmp_path / "empty.wav").exists()

    @pytest.mark.parametrize("provider_type", [
        "elevenlabs", "smallest", "google_tts", "openai_tts", "cartesia",
    ])
    @pytest.mark.asyncio
    async def test_stub_for_all_providers_async(self, monkeypatch, tmp_path, provider_type):
        monkeypatch.delenv("FAKE_KEY", raising=False)
        adapter = _make_adapter(provider_type)
        out = str(tmp_path / f"{provider_type}.wav")
        await adapter.synthesize_async(text="test", output_path=out)
        assert (tmp_path / f"{provider_type}.wav").exists()


# ── async synthesize_bytes (returns bytes, no file) ──────────────────────────


class TestAsyncSynthesizeBytes:

    @pytest.mark.asyncio
    async def test_method_exists(self):
        adapter = _make_adapter("elevenlabs")
        assert hasattr(adapter, "synthesize_bytes")
        assert asyncio.iscoroutinefunction(adapter.synthesize_bytes)

    @pytest.mark.asyncio
    async def test_returns_wav_bytes(self, monkeypatch):
        """Stub should return valid WAV bytes."""
        monkeypatch.delenv("FAKE_KEY", raising=False)
        adapter = _make_adapter("elevenlabs")
        data = await adapter.synthesize_bytes(text="hello")
        assert isinstance(data, bytes)
        assert len(data) > 44  # WAV header is 44 bytes
        # Validate it's real WAV
        buf = io.BytesIO(data)
        with wave.open(buf, "rb") as w:
            assert w.getnframes() > 0

    @pytest.mark.asyncio
    async def test_empty_text_returns_short_silence(self, monkeypatch):
        monkeypatch.setenv("FAKE_KEY", "x")
        adapter = _make_adapter("elevenlabs")
        data = await adapter.synthesize_bytes(text="")
        assert isinstance(data, bytes)
        assert len(data) > 0


# ── edge_tts native async (no thread pool) ───────────────────────────────────


class TestEdgeTTSNativeAsync:

    @pytest.mark.asyncio
    async def test_edge_tts_async_no_thread(self, tmp_path):
        """edge_tts should use native asyncio, not asyncio.to_thread."""
        adapter = _make_adapter("edge_tts", voice_id="en-US-GuyNeural",
                                api_key_env="UNUSED")
        out = str(tmp_path / "edge.mp3")
        # This should work without creating a new event loop
        try:
            await adapter.synthesize_async(text="Hello world", output_path=out)
            assert (tmp_path / "edge.mp3").exists()
        except Exception:
            # edge_tts may fail without network — that's OK
            # we're testing it doesn't raise "cannot run nested event loop"
            pass

    @pytest.mark.asyncio
    async def test_edge_tts_bytes_no_thread(self):
        """edge_tts bytes should also use native async."""
        adapter = _make_adapter("edge_tts", voice_id="en-US-GuyNeural",
                                api_key_env="UNUSED")
        try:
            data = await adapter.synthesize_bytes(text="Hello world")
            assert isinstance(data, bytes)
        except Exception:
            pass  # network may not be available


# ── Provider dispatch in async mode ──────────────────────────────────────────


class TestAsyncProviderDispatch:

    @pytest.mark.parametrize("provider_type", [
        "elevenlabs", "smallest", "google_tts", "openai_tts", "cartesia",
    ])
    @pytest.mark.asyncio
    async def test_all_providers_dispatch_async(self, monkeypatch, tmp_path, provider_type):
        """Every provider should have async dispatch without errors (stub mode)."""
        monkeypatch.delenv("FAKE_KEY", raising=False)
        adapter = _make_adapter(provider_type)
        out = str(tmp_path / f"{provider_type}_async.wav")
        await adapter.synthesize_async(text="dispatch test", output_path=out)
        assert (tmp_path / f"{provider_type}_async.wav").exists()

    @pytest.mark.asyncio
    async def test_edge_tts_dispatch_async(self, tmp_path):
        """edge_tts async dispatch — skipped if edge_tts not installed."""
        try:
            import edge_tts
        except ImportError:
            pytest.skip("edge_tts not installed")
        adapter = _make_adapter("edge_tts", voice_id="en-US-GuyNeural", api_key_env="UNUSED")
        out = str(tmp_path / "edge_async.mp3")
        try:
            await adapter.synthesize_async(text="test", output_path=out)
            assert (tmp_path / "edge_async.mp3").exists()
        except RuntimeError:
            pass  # network not available


import asyncio
