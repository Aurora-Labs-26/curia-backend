"""
tests/test_tts_deepgram.py
Deepgram Aura TTS adapter — request shape (sync + async), error handling,
config wiring. httpx is mocked; no network or API key needed.
"""

import os
import tempfile
from unittest.mock import patch

import pytest

from core.llm_config.resolver import reload_config
from core.llm_config.adapters.tts import build_tts


@pytest.fixture()
def adapter():
    cfg = reload_config()
    return build_tts(
        cfg.providers["deepgram"],
        cfg.models["deepgram-aura"],
        cfg.models["deepgram-aura"].defaults or {},
        voice_id="aura-2-thalia-en",
    )


class _Resp:
    def __init__(self, status=200, content=b"ID3MP3", text=""):
        self.status_code = status
        self.content = content
        self.text = text


def test_config_registers_deepgram():
    cfg = reload_config()
    assert cfg.providers["deepgram"].type == "deepgram"
    assert cfg.models["deepgram-aura"].kind == "tts"


def test_output_format_is_mp3(adapter):
    assert adapter.output_format == "mp3"


def test_sync_request_shape(adapter):
    captured = {}

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, params=None, headers=None, json=None):
            captured.update(url=url, params=params, headers=headers, json=json)
            return _Resp()

    with patch("core.llm_config.adapters.tts.httpx.Client", FakeClient):
        out = tempfile.mktemp(suffix=".mp3")
        adapter._synthesize_deepgram("hello", out, "dg_key")
        assert os.path.getsize(out) > 0

    assert captured["url"] == "https://api.deepgram.com/v1/speak"
    assert captured["params"]["model"] == "aura-2-thalia-en"
    assert captured["params"]["encoding"] == "mp3"
    assert captured["params"]["bit_rate"] == 48000
    assert captured["headers"]["Authorization"] == "Token dg_key"
    assert captured["json"] == {"text": "hello"}


def test_voice_id_overrides_model(adapter):
    """The speaker's voice_id is the Deepgram model param, not the alias model_id."""
    captured = {}

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, params=None, headers=None, json=None):
            captured.update(params=params)
            return _Resp()

    adapter.voice_id = "aura-2-orion-en"
    with patch("core.llm_config.adapters.tts.httpx.Client", FakeClient):
        adapter._synthesize_deepgram("x", tempfile.mktemp(suffix=".mp3"), "k")
    assert captured["params"]["model"] == "aura-2-orion-en"


def test_long_text_is_chunked_under_2000(adapter):
    """Deepgram caps input at 2000 chars/request — long text must split into multiple calls."""
    from core.llm_config.adapters.tts import TTSAdapter

    long_text = ("This is a sentence about testing. " * 200).strip()  # ~6600 chars
    chunks = TTSAdapter._chunk_for_deepgram(long_text)
    assert len(chunks) > 1
    assert all(len(c) <= 1800 for c in chunks)
    assert sum(len(c) for c in chunks) >= len(long_text) - len(chunks) * 2  # ~no text dropped

    calls = []

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, params=None, headers=None, json=None):
            calls.append(json["text"])
            return _Resp(content=b"\xff\xf3SEG")

    with patch("core.llm_config.adapters.tts.httpx.Client", FakeClient):
        out = tempfile.mktemp(suffix=".mp3")
        adapter._synthesize_deepgram(long_text, out, "k")
        # one call per chunk, segments concatenated
        assert len(calls) == len(chunks)
        assert os.path.getsize(out) == len(b"\xff\xf3SEG") * len(chunks)


def test_error_status_raises(adapter):
    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **k):
            return _Resp(status=401, text="Unauthorized")

    with patch("core.llm_config.adapters.tts.httpx.Client", FakeClient):
        with pytest.raises(RuntimeError, match="Deepgram"):
            adapter._synthesize_deepgram("x", tempfile.mktemp(suffix=".mp3"), "bad")


async def test_async_request_shape(adapter):
    captured = {}

    class FakeAsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, params=None, headers=None, json=None):
            captured.update(url=url, params=params, headers=headers, json=json)
            return _Resp()

    with patch("core.llm_config.adapters.tts.httpx.AsyncClient", FakeAsyncClient):
        out = tempfile.mktemp(suffix=".mp3")
        await adapter._async_deepgram("hi", out, "dg_key")
        assert os.path.getsize(out) > 0

    assert captured["url"].endswith("/v1/speak")
    assert captured["params"]["model"] == "aura-2-thalia-en"
    assert captured["headers"]["Authorization"] == "Token dg_key"
    assert captured["json"] == {"text": "hi"}
