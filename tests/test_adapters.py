"""
tests/test_adapters.py
Multi-provider adapter dispatch — LLM, embedding, TTS.
No network calls, no API keys.
"""
import pytest
from core.llm_config.schema import ModelConfig, ProviderConfig
from core.llm_config.adapters.embedding import Embedder
from core.llm_config.adapters.tts import TTSAdapter


# ── Embedding adapters ────────────────────────────────────────────────────────

def _make_embedder(provider_type: str, dimension: int = 1024) -> Embedder:
    return Embedder(
        provider=ProviderConfig(type=provider_type, api_key_env="FAKE_KEY"),
        model=ModelConfig(provider="any", model_id="any", kind="embedding", dimension=dimension),
        settings={},
    )


@pytest.mark.parametrize("provider_type", [
    "voyage", "openai", "cohere", "jina", "mistral", "gemini",
])
@pytest.mark.asyncio
async def test_embedding_stub_when_no_key(monkeypatch, provider_type):
    """No API key -> zero-vector stub for every embedding provider."""
    monkeypatch.delenv("FAKE_KEY", raising=False)
    embedder = _make_embedder(provider_type, dimension=512)
    result = await embedder.embed("test text")
    assert result is not None
    assert len(result) == 512
    assert all(v == 0.0 for v in result)


@pytest.mark.asyncio
async def test_embedding_empty_text_returns_stub(monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "x")
    embedder = _make_embedder("voyage")
    result = await embedder.embed("")
    assert result is not None
    assert len(result) == 1024
    assert all(v == 0.0 for v in result)


@pytest.mark.asyncio
async def test_embedding_dimension_from_config():
    embedder = _make_embedder("openai", dimension=3072)
    assert embedder.dimension == 3072


# ── TTS adapters ──────────────────────────────────────────────────────────────

def _make_tts(provider_type: str, **settings) -> TTSAdapter:
    return TTSAdapter(
        provider=ProviderConfig(type=provider_type, api_key_env="FAKE_KEY"),
        model=ModelConfig(provider="any", model_id="any", kind="tts"),
        settings=settings,
        voice_id="test_voice",
    )


@pytest.mark.parametrize("provider_type", [
    "elevenlabs", "smallest", "google_tts", "xai", "openai_tts", "cartesia",
])
def test_tts_stub_when_no_key(monkeypatch, tmp_path, provider_type):
    """No API key -> silent WAV stub for all non-edge providers."""
    monkeypatch.delenv("FAKE_KEY", raising=False)
    adapter = _make_tts(provider_type)
    out = tmp_path / "out.wav"
    adapter.synthesize(text="hello", output_path=str(out))
    assert out.exists()


def test_tts_output_format_openai_tts_mp3():
    adapter = _make_tts("openai_tts", response_format="mp3")
    assert adapter.output_format == "mp3"


def test_tts_output_format_openai_tts_wav():
    adapter = _make_tts("openai_tts", response_format="wav")
    assert adapter.output_format == "wav"


def test_tts_output_format_cartesia():
    adapter = _make_tts("cartesia")
    assert adapter.output_format == "wav"


def test_tts_output_format_edge():
    adapter = _make_tts("edge_tts")
    assert adapter.output_format == "mp3"


# ── LLM adapters ──────────────────────────────────────────────────────────────

def test_llm_build_raises_without_key(monkeypatch):
    """LLMs do not have stubs — must fail without API key."""
    monkeypatch.delenv("FAKE_KEY", raising=False)
    from core.llm_config.adapters.llm import build_llm
    with pytest.raises(RuntimeError, match="not set"):
        build_llm(
            provider=ProviderConfig(type="anthropic", api_key_env="FAKE_KEY"),
            model=ModelConfig(provider="anthropic", model_id="test", kind="llm"),
            settings={},
        )


def test_llm_build_rejects_wrong_kind():
    from core.llm_config.adapters.llm import build_llm
    with pytest.raises(ValueError, match="expected 'llm'"):
        build_llm(
            provider=ProviderConfig(type="anthropic", api_key_env="FAKE_KEY"),
            model=ModelConfig(provider="anthropic", model_id="test", kind="tts"),
            settings={},
        )


def test_vllm_requires_base_url(monkeypatch):
    """vLLM provider must have base_url configured."""
    monkeypatch.setenv("FAKE_KEY", "x")
    from core.llm_config.adapters.llm import build_llm
    with pytest.raises(RuntimeError, match="base_url"):
        build_llm(
            provider=ProviderConfig(type="vllm", api_key_env="FAKE_KEY"),
            model=ModelConfig(provider="vllm", model_id="llama-70b", kind="llm"),
            settings={},
        )
