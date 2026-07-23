"""
tests/test_tts_cartesia.py
Cartesia Sonic TTS adapter — the synth method predates this branch; these tests
cover it now that the provider/model config is active. httpx mocked.
"""

import os
import tempfile
from unittest.mock import patch

import pytest

from core.llm_config.adapters.tts import build_tts
from core.llm_config.resolver import reload_config

VOICE = "0e21713a-5e9a-428a-bed4-90d410b87f13"


@pytest.fixture()
def adapter():
    cfg = reload_config()
    return build_tts(
        cfg.providers["cartesia"],
        cfg.models["cartesia-sonic"],
        cfg.models["cartesia-sonic"].defaults or {},
        voice_id=VOICE,
    )


class _Resp:
    def __init__(self, status=200, content=b"RIFFxxxxWAVE", text=""):
        self.status_code = status
        self.content = content
        self.text = text


def test_config_registers_cartesia():
    cfg = reload_config()
    assert cfg.providers["cartesia"].type == "cartesia"
    assert cfg.providers["cartesia"].api_key_env == "CARTESIA_API_KEY"
    assert cfg.models["cartesia-sonic"].kind == "tts"
    assert cfg.models["cartesia-sonic"].model_id == "sonic-3.5"


def test_output_format_is_wav(adapter):
    assert adapter.output_format == "wav"


def test_all_speakers_bound_to_cartesia():
    cfg = reload_config()
    voice_ids = set()
    for name in ("kenji", "arjun", "emeka"):
        sp = cfg.bindings.speaker[name]
        assert sp.model == "cartesia-sonic", f"{name} not on cartesia"
        voice_ids.add(sp.voice_id)
    assert len(voice_ids) == 3, "speakers must have distinct voices"


def test_sync_request_shape(adapter):
    captured = {}

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, headers=None, json=None):
            captured.update(url=url, headers=headers, json=json)
            return _Resp()

    with patch("core.llm_config.adapters.tts.httpx.Client", FakeClient):
        out = tempfile.mktemp(suffix=".wav")
        adapter._synthesize_cartesia("hello", out, "ca_key")
        assert os.path.getsize(out) > 0

    assert captured["url"].endswith("/tts/bytes")
    assert captured["headers"]["X-API-Key"] == "ca_key"
    assert captured["headers"]["Cartesia-Version"] == "2026-03-01"
    body = captured["json"]
    assert body["model_id"] == "sonic-3.5"
    assert body["voice"] == {"mode": "id", "id": VOICE}
    assert body["output_format"]["container"] == "wav"
    assert body["output_format"]["sample_rate"] == 22050


def test_error_status_raises(adapter):
    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **k):
            return _Resp(status=401, text="bad key")

    with patch("core.llm_config.adapters.tts.httpx.Client", FakeClient):
        with pytest.raises(RuntimeError, match="401"):
            adapter._synthesize_cartesia("hello", tempfile.mktemp(suffix=".wav"), "ca_key")


# ---------------------------------------------------------------------------
# Streamed-WAV header repair — Cartesia emits placeholder RIFF sizes
# (0xFFFFFFFF) because it streams; wave-module consumers would misread.
# ---------------------------------------------------------------------------

import io
import struct
import wave

from core.llm_config.adapters.tts import _fix_streamed_wav_header


def _wav_with_bogus_sizes(n_frames: int = 2205, sample_rate: int = 22050) -> bytes:
    """A real WAV whose RIFF and data sizes are overwritten with 0xFFFFFFFF."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * n_frames)
    blob = bytearray(buf.getvalue())
    blob[4:8] = struct.pack("<I", 0xFFFFFFFF)               # RIFF size
    data_idx = blob.find(b"data")
    blob[data_idx + 4:data_idx + 8] = struct.pack("<I", 0xFFFFFFFF)  # data size
    return bytes(blob)


def test_fix_streamed_wav_header_repairs_bogus_sizes():
    path = tempfile.mktemp(suffix=".wav")
    with open(path, "wb") as f:
        f.write(_wav_with_bogus_sizes(n_frames=2205))

    # before: wave reports a nonsense frame count
    with wave.open(path, "rb") as w:
        assert w.getnframes() != 2205

    _fix_streamed_wav_header(path)
    with wave.open(path, "rb") as w:
        assert w.getnframes() == 2205
        assert w.getframerate() == 22050


def test_fix_streamed_wav_header_noop_on_wellformed():
    path = tempfile.mktemp(suffix=".wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
        w.writeframes(b"\x00\x00" * 100)
    before = open(path, "rb").read()
    _fix_streamed_wav_header(path)
    assert open(path, "rb").read() == before


def test_fix_streamed_wav_header_noop_on_non_wav():
    path = tempfile.mktemp(suffix=".bin")
    with open(path, "wb") as f:
        f.write(b"ID3not-a-wav-at-all" * 10)
    before = open(path, "rb").read()
    _fix_streamed_wav_header(path)
    assert open(path, "rb").read() == before


def test_synthesize_repairs_header_end_to_end(adapter):
    bogus = _wav_with_bogus_sizes(n_frames=441)

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **k):
            return _Resp(content=bogus)

    with patch("core.llm_config.adapters.tts.httpx.Client", FakeClient):
        out = tempfile.mktemp(suffix=".wav")
        adapter._synthesize_cartesia("hello", out, "ca_key")

    with wave.open(out, "rb") as w:                # readable by wave = repaired
        assert w.getnframes() == 441
