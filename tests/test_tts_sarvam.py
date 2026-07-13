"""
tests/test_tts_sarvam.py
Sarvam AI Bulbul TTS adapter — request shape (sync + async), chunking, base64
WAV decode + frame-level concat, error handling, config wiring. httpx is
mocked; no network or API key needed.
"""

import base64
import io
import os
import tempfile
import wave
from unittest.mock import patch

import pytest

from core.llm_config.adapters.tts import build_tts
from core.llm_config.resolver import reload_config


def _tiny_wav(n_frames: int = 220, sample_rate: int = 22050) -> bytes:
    """A valid in-memory mono 16-bit WAV of n_frames silence."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * n_frames)
    return buf.getvalue()


def _resp_payload(*wavs: bytes) -> dict:
    return {"audios": [base64.b64encode(w).decode() for w in wavs]}


@pytest.fixture()
def adapter():
    cfg = reload_config()
    return build_tts(
        cfg.providers["sarvam"],
        cfg.models["sarvam-bulbul"],
        cfg.models["sarvam-bulbul"].defaults or {},
        voice_id="anushka",
    )


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_config_registers_sarvam():
    cfg = reload_config()
    assert cfg.providers["sarvam"].type == "sarvam"
    assert cfg.providers["sarvam"].api_key_env == "SARVAM_API_KEY"
    assert cfg.models["sarvam-bulbul"].kind == "tts"
    assert cfg.models["sarvam-bulbul"].model_id == "bulbul:v2"


def test_output_format_is_wav(adapter):
    # sarvam is not in the MP3 provider list — WAV path
    assert adapter.output_format == "wav"


def test_sync_request_shape(adapter):
    captured = {}

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, headers=None, json=None):
            captured.update(url=url, headers=headers, json=json)
            return _Resp(payload=_resp_payload(_tiny_wav()))

    with patch("core.llm_config.adapters.tts.httpx.Client", FakeClient):
        out = tempfile.mktemp(suffix=".wav")
        adapter._synthesize_sarvam("namaste world", out, "sv_key")
        assert os.path.getsize(out) > 44  # more than a bare WAV header

    assert captured["url"].endswith("/text-to-speech")
    assert captured["headers"]["api-subscription-key"] == "sv_key"
    body = captured["json"]
    assert body["text"] == "namaste world"
    assert body["model"] == "bulbul:v2"
    assert body["speaker"] == "anushka"
    assert body["target_language_code"] == "en-IN"
    assert body["speech_sample_rate"] == 22050


def test_long_text_chunks_and_concatenates(adapter):
    calls = []
    frames_per_call = 300

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, headers=None, json=None):
            calls.append(json["text"])
            return _Resp(payload=_resp_payload(_tiny_wav(frames_per_call)))

    long_text = ". ".join(["This is a fairly long sentence about many topics"] * 40)
    with patch("core.llm_config.adapters.tts.httpx.Client", FakeClient):
        out = tempfile.mktemp(suffix=".wav")
        adapter._synthesize_sarvam(long_text, out, "sv_key")

    assert len(calls) > 1                                  # actually chunked
    assert all(len(c) <= 450 for c in calls)               # respects max_chars
    with wave.open(out, "rb") as w:                        # frame-level concat
        assert w.getnframes() == frames_per_call * len(calls)
        assert w.getframerate() == 22050


def test_error_status_raises(adapter):
    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **k):
            return _Resp(status=429, text="rate limited")

    with patch("core.llm_config.adapters.tts.httpx.Client", FakeClient):
        with pytest.raises(RuntimeError, match="429"):
            adapter._synthesize_sarvam("hello", tempfile.mktemp(suffix=".wav"), "sv_key")


def test_empty_audios_raises(adapter):
    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **k):
            return _Resp(payload={"audios": []})

    with patch("core.llm_config.adapters.tts.httpx.Client", FakeClient):
        with pytest.raises(RuntimeError, match="no audio"):
            adapter._synthesize_sarvam("hello", tempfile.mktemp(suffix=".wav"), "sv_key")


async def test_async_request_shape(adapter):
    captured = {}

    class FakeAsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            captured.update(url=url, headers=headers, json=json)
            return _Resp(payload=_resp_payload(_tiny_wav()))

    with patch("core.llm_config.adapters.tts.httpx.AsyncClient", FakeAsyncClient):
        out = tempfile.mktemp(suffix=".wav")
        await adapter._async_sarvam("hello async", out, "sv_key")
        assert os.path.getsize(out) > 44

    assert captured["headers"]["api-subscription-key"] == "sv_key"
    assert captured["json"]["text"] == "hello async"
