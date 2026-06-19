"""
tests/test_llm_builder.py
Tests for core/llm_config/adapters/llm.py — LLM construction for each provider.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.llm_config.schema import ModelConfig, ProviderConfig
from core.llm_config.adapters.llm import build_llm


def _model(kind="llm"):
    return ModelConfig(provider="test", model_id="test-model-id", kind=kind)


def _provider(ptype="anthropic"):
    return ProviderConfig(type=ptype, api_key_env="TEST_API_KEY")


class TestBuildLlmValidation:
    def test_wrong_kind_raises(self):
        with pytest.raises(ValueError, match="expected 'llm'"):
            build_llm(_provider(), _model(kind="embedding"), {})

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_api_key_raises(self):
        with pytest.raises(RuntimeError, match="TEST_API_KEY is not set"):
            build_llm(_provider(), _model(), {})


class TestBuildLlmProviders:
    @patch.dict("os.environ", {"TEST_API_KEY": "key-123"})
    @patch("core.llm_config.adapters.llm.dspy")
    def test_anthropic(self, mock_dspy):
        mock_dspy.LM.return_value = MagicMock()
        lm = build_llm(_provider("anthropic"), _model(), {"temperature": 0.7})
        mock_dspy.LM.assert_called_once()
        call_args = mock_dspy.LM.call_args
        assert "anthropic/" in call_args[0][0]
        assert call_args[1]["api_key"] == "key-123"
        assert call_args[1]["temperature"] == 0.7

    @patch.dict("os.environ", {"TEST_API_KEY": "key-123"})
    @patch("core.llm_config.adapters.llm.dspy")
    def test_openai(self, mock_dspy):
        mock_dspy.LM.return_value = MagicMock()
        build_llm(_provider("openai"), _model(), {})
        assert "openai/" in mock_dspy.LM.call_args[0][0]

    @patch.dict("os.environ", {"TEST_API_KEY": "key-123"})
    @patch("core.llm_config.adapters.llm.dspy")
    def test_cohere(self, mock_dspy):
        mock_dspy.LM.return_value = MagicMock()
        build_llm(_provider("cohere"), _model(), {})
        assert "cohere/" in mock_dspy.LM.call_args[0][0]

    @patch.dict("os.environ", {"TEST_API_KEY": "key-123"})
    @patch("core.llm_config.adapters.llm.dspy")
    def test_vllm_requires_base_url(self, mock_dspy):
        p = ProviderConfig(type="vllm", api_key_env="TEST_API_KEY")
        with pytest.raises(RuntimeError, match="base_url"):
            build_llm(p, _model(), {})

    @patch.dict("os.environ", {"TEST_API_KEY": "key-123"})
    @patch("core.llm_config.adapters.llm.dspy")
    def test_vllm_with_base_url(self, mock_dspy):
        mock_dspy.LM.return_value = MagicMock()
        p = ProviderConfig(type="vllm", api_key_env="TEST_API_KEY", base_url="http://localhost:8001/v1")
        build_llm(p, _model(), {})
        assert mock_dspy.LM.call_args[1]["api_base"] == "http://localhost:8001/v1"

    @patch.dict("os.environ", {"TEST_API_KEY": "key-123"})
    @patch("core.llm_config.adapters.llm.dspy")
    def test_xai_llm(self, mock_dspy):
        mock_dspy.LM.return_value = MagicMock()
        build_llm(_provider("xai_llm"), _model(), {})
        assert mock_dspy.LM.call_args[1]["api_base"] == "https://api.x.ai/v1"

    @patch.dict("os.environ", {"TEST_API_KEY": "key-123"})
    @patch("core.llm_config.adapters.llm.dspy")
    def test_gemini(self, mock_dspy):
        mock_dspy.LM.return_value = MagicMock()
        build_llm(_provider("gemini"), _model(), {})
        assert "google/" in mock_dspy.LM.call_args[0][0]

    @patch.dict("os.environ", {"TEST_API_KEY": "key-123"})
    @patch("core.llm_config.adapters.llm.dspy")
    def test_openrouter(self, mock_dspy):
        mock_dspy.LM.return_value = MagicMock()
        build_llm(_provider("openrouter"), _model(), {})
        assert "openrouter/" in mock_dspy.LM.call_args[0][0]
        assert mock_dspy.LM.call_args[1]["api_base"] == "https://openrouter.ai/api/v1"

    @patch.dict("os.environ", {"TEST_API_KEY": "key-123"})
    def test_unknown_provider_raises(self):
        # "elevenlabs" is a valid ProviderType but not handled in build_llm (it's TTS-only)
        with pytest.raises(ValueError, match="not supported"):
            build_llm(_provider("elevenlabs"), _model(), {})
