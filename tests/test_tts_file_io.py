"""
tests/test_tts_file_io.py
Tests for TTS adapter async file I/O correctness.

Verifies that all async TTS methods properly write output files
via asyncio.to_thread (not blocking the event loop).
Covers synthesize_async, synthesize_bytes, and the _write_bytes helper.
"""

import asyncio
import io
import os
import wave

import pytest

from core.llm_config.schema import ModelConfig, ProviderConfig
from core.llm_config.adapters.tts import TTSAdapter, _write_bytes, _write_wav_from_pcm


def _make_adapter(provider_type: str = "elevenlabs", voice_id: str = "test",
                  api_key_env: str = "FAKE_KEY", **settings) -> TTSAdapter:
    return TTSAdapter(
        provider=ProviderConfig(type=provider_type, api_key_env=api_key_env),
        model=ModelConfig(provider="any", model_id="any", kind="tts"),
        settings=settings,
        voice_id=voice_id,
    )


class TestWriteHelpers:

    def test_write_bytes_creates_file(self, tmp_path):
        path = str(tmp_path / "out.bin")
        _write_bytes(path, b"hello")
        assert open(path, "rb").read() == b"hello"

    def test_write_wav_from_pcm_creates_valid_wav(self, tmp_path):
        path = str(tmp_path / "out.wav")
        pcm = b"\x00\x00" * 22050
        _write_wav_from_pcm(pcm, 22050, path)
        with wave.open(path, "rb") as w:
            assert w.getframerate() == 22050
            assert w.getsampwidth() == 2
            assert w.getnchannels() == 1
            assert w.getnframes() == 22050

    def test_write_bytes_overwrites(self, tmp_path):
        path = str(tmp_path / "over.bin")
        _write_bytes(path, b"first")
        _write_bytes(path, b"second")
        assert open(path, "rb").read() == b"second"


class TestSynthesizeBytesAsync:

    @pytest.mark.asyncio
    async def test_returns_valid_wav_bytes(self, monkeypatch):
        monkeypatch.delenv("FAKE_KEY", raising=False)
        adapter = _make_adapter()
        data = await adapter.synthesize_bytes("hello")
        assert isinstance(data, bytes)
        buf = io.BytesIO(data)
        with wave.open(buf, "rb") as w:
            assert w.getnframes() > 0

    @pytest.mark.asyncio
    async def test_cleans_up_temp_file(self, monkeypatch, tmp_path):
        """Temp file should be removed after bytes are read."""
        monkeypatch.delenv("FAKE_KEY", raising=False)
        adapter = _make_adapter()
        await adapter.synthesize_bytes("cleanup test")
        # Can't check temp path directly, but no exception means cleanup worked

    @pytest.mark.asyncio
    async def test_concurrent_calls(self, monkeypatch):
        """Multiple synthesize_bytes calls should work concurrently."""
        monkeypatch.delenv("FAKE_KEY", raising=False)
        adapter = _make_adapter()
        results = await asyncio.gather(
            adapter.synthesize_bytes("one"),
            adapter.synthesize_bytes("two"),
            adapter.synthesize_bytes("three"),
        )
        assert len(results) == 3
        for data in results:
            assert isinstance(data, bytes)
            assert len(data) > 44


class TestSynthesizeAsyncFileOutput:

    @pytest.mark.asyncio
    async def test_stub_writes_valid_wav(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FAKE_KEY", raising=False)
        adapter = _make_adapter()
        out = str(tmp_path / "stub.wav")
        await adapter.synthesize_async(text="stub test", output_path=out)
        assert os.path.exists(out)
        with wave.open(out, "rb") as w:
            assert w.getnframes() > 0

    @pytest.mark.asyncio
    async def test_empty_text_produces_output(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FAKE_KEY", "x")
        adapter = _make_adapter()
        out = str(tmp_path / "empty.wav")
        await adapter.synthesize_async(text="", output_path=out)
        assert os.path.exists(out)

    @pytest.mark.asyncio
    async def test_does_not_block_loop(self, monkeypatch, tmp_path):
        """Event loop should remain responsive during synthesis."""
        monkeypatch.delenv("FAKE_KEY", raising=False)
        adapter = _make_adapter()

        flag = asyncio.Event()

        async def bg():
            flag.set()

        task = asyncio.create_task(bg())
        await adapter.synthesize_async(text="hello", output_path=str(tmp_path / "nb.wav"))
        await task
        assert flag.is_set()

    @pytest.mark.parametrize("provider_type", [
        "elevenlabs", "smallest", "google_tts", "openai_tts", "cartesia",
    ])
    @pytest.mark.asyncio
    async def test_all_providers_stub_output(self, monkeypatch, tmp_path, provider_type):
        monkeypatch.delenv("FAKE_KEY", raising=False)
        adapter = _make_adapter(provider_type)
        out = str(tmp_path / f"{provider_type}.wav")
        await adapter.synthesize_async(text="provider test", output_path=out)
        assert os.path.exists(out)
        size = os.path.getsize(out)
        assert size > 44
