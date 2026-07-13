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
    assert cfg.models["cartesia-sonic"].model_id == "sonic-2"


def test_output_format_is_wav(adapter):
    assert adapter.output_format == "wav"


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
    body = captured["json"]
    assert body["model_id"] == "sonic-2"
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
