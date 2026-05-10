"""
tests/test_tts_providers.py
TTS adapter dispatch + stub fallback. Pure Python, no network.
"""

import os
import wave
from pathlib import Path

import pytest

from core.llm_config.adapters.tts import TTSAdapter, _write_silent_wav
from core.llm_config.schema import ModelConfig, ProviderConfig


def _make_adapter(provider_type: str, voice_id: str = "test_voice", api_key_env: str = "FAKE_KEY") -> TTSAdapter:
    return TTSAdapter(
        provider=ProviderConfig(type=provider_type, api_key_env=api_key_env),
        model=ModelConfig(provider="any", model_id="any", kind="tts"),
        settings={},
        voice_id=voice_id,
    )


def test_silent_wav_helper_writes_valid_file(tmp_path):
    out = tmp_path / "silence.wav"
    _write_silent_wav(str(out), duration_seconds=0.5)
    assert out.exists()
    with wave.open(str(out), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 22050
        # ~0.5s of audio at 22050 → ~11025 frames (allow rounding fudge)
        assert 10000 <= w.getnframes() <= 12000


@pytest.mark.parametrize(
    "provider_type",
    ["elevenlabs", "smallest", "google_tts", "xai"],
)
def test_stub_fallback_when_no_key(monkeypatch, tmp_path, provider_type):
    """No API key → silent WAV stub for every provider type."""
    monkeypatch.delenv("FAKE_KEY", raising=False)
    out = tmp_path / "out.wav"
    adapter = _make_adapter(provider_type)
    adapter.synthesize(text="hello world", output_path=str(out))
    assert out.exists()
    # Validate it's a real WAV (not an HTTP error blob, not empty)
    with wave.open(str(out), "rb") as w:
        assert w.getnframes() > 0


def test_empty_text_writes_short_silence(monkeypatch, tmp_path):
    """Empty/whitespace text → short silent WAV regardless of key state."""
    monkeypatch.setenv("FAKE_KEY", "x")
    out = tmp_path / "empty.wav"
    adapter = _make_adapter("elevenlabs")
    adapter.synthesize(text="   ", output_path=str(out))
    assert out.exists()
    with wave.open(str(out), "rb") as w:
        # ~0.3s of silence → ~6615 frames
        assert 5000 <= w.getnframes() <= 8000


def test_xai_with_key_but_no_endpoint_raises(monkeypatch, tmp_path):
    """xAI stub raises NotImplementedError if endpoint_path is not configured."""
    monkeypatch.setenv("FAKE_KEY", "test")
    adapter = _make_adapter("xai")
    out = tmp_path / "out.wav"
    with pytest.raises(NotImplementedError, match="endpoint_path"):
        adapter.synthesize(text="hi", output_path=str(out))


def test_unknown_provider_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_KEY", "test")
    out = tmp_path / "out.wav"
    # Bypass the literal validator by constructing dataclass directly via raw values
    # (we only test dispatch — schema validation is covered elsewhere).
    adapter = TTSAdapter(
        provider=ProviderConfig.model_construct(  # type: ignore[arg-type]
            type="not_a_real_provider",
            api_key_env="FAKE_KEY",
        ),
        model=ModelConfig(provider="any", model_id="any", kind="tts"),
        settings={},
        voice_id="x",
    )
    with pytest.raises(ValueError, match="not implemented"):
        adapter.synthesize(text="hi", output_path=str(out))
