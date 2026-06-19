"""
tests/test_resolver.py
Tests for core/llm_config/resolver.py — binding resolution, scope hierarchy, merge.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.llm_config.schema import (
    BindingValue,
    Bindings,
    CuriaConfig,
    ModelConfig,
    ProviderConfig,
)
from core.llm_config.resolver import (
    _merge_settings,
    _provider_and_model,
    _resolve_binding,
    _resolve_speaker_binding,
)


def _test_config(**binding_overrides):
    """Build a minimal CuriaConfig for resolver tests."""
    providers = {
        "anthropic": ProviderConfig(type="anthropic", api_key_env="ANTHROPIC_KEY"),
        "edge": ProviderConfig(type="edge_tts", api_key_env="EDGE_KEY"),
    }
    models = {
        "sonnet": ModelConfig(provider="anthropic", model_id="claude-sonnet-4-6", kind="llm", defaults={"temperature": 0.7}),
        "haiku": ModelConfig(provider="anthropic", model_id="claude-haiku-4-5", kind="llm"),
        "edge-tts": ModelConfig(provider="edge", model_id="edge-tts-1", kind="tts"),
    }
    base_bindings = {
        "task": {"transcript": BindingValue(model="sonnet"), "outline": BindingValue(model="haiku")},
        "speaker": {"kenji": BindingValue(model="edge-tts", voice_id="v1")},
    }
    base_bindings.update(binding_overrides)
    bindings = Bindings(**base_bindings)
    return CuriaConfig(providers=providers, models=models, bindings=bindings)


# ---------------------------------------------------------------------------
# _merge_settings
# ---------------------------------------------------------------------------


class TestMergeSettings:
    def test_model_defaults_only(self):
        model = ModelConfig(provider="p", model_id="m", kind="llm", defaults={"temperature": 0.5})
        binding = BindingValue(model="m")
        result = _merge_settings(model, binding)
        assert result == {"temperature": 0.5}

    def test_overrides_win(self):
        model = ModelConfig(provider="p", model_id="m", kind="llm", defaults={"temperature": 0.5})
        binding = BindingValue(model="m", overrides={"temperature": 0.9})
        result = _merge_settings(model, binding)
        assert result["temperature"] == 0.9

    def test_merged(self):
        model = ModelConfig(provider="p", model_id="m", kind="llm", defaults={"temperature": 0.5})
        binding = BindingValue(model="m", overrides={"max_tokens": 1000})
        result = _merge_settings(model, binding)
        assert result == {"temperature": 0.5, "max_tokens": 1000}


# ---------------------------------------------------------------------------
# _resolve_binding — scope hierarchy
# ---------------------------------------------------------------------------


class TestResolveBinding:
    def test_task_default(self):
        cfg = _test_config()
        with patch("core.llm_config.resolver.get_config", return_value=cfg), \
             patch("core.llm_config.resolver._env", return_value="dev"):
            bv = _resolve_binding("transcript")
        assert bv.model == "sonnet"

    def test_unknown_task_raises(self):
        cfg = _test_config()
        with patch("core.llm_config.resolver.get_config", return_value=cfg), \
             patch("core.llm_config.resolver._env", return_value="dev"):
            with pytest.raises(ValueError, match="No binding"):
                _resolve_binding("nonexistent_task")

    def test_environment_overrides_task(self):
        cfg = _test_config(environment={"dev": {"transcript": BindingValue(model="haiku")}})
        with patch("core.llm_config.resolver.get_config", return_value=cfg), \
             patch("core.llm_config.resolver._env", return_value="dev"):
            bv = _resolve_binding("transcript")
        assert bv.model == "haiku"

    def test_show_overrides_environment(self):
        cfg = _test_config(
            environment={"dev": {"transcript": BindingValue(model="haiku")}},
            show={"clarity_engine": {"transcript": BindingValue(model="sonnet")}},
        )
        with patch("core.llm_config.resolver.get_config", return_value=cfg), \
             patch("core.llm_config.resolver._env", return_value="dev"):
            bv = _resolve_binding("transcript", show="clarity_engine")
        assert bv.model == "sonnet"

    def test_user_overrides_all(self):
        cfg = _test_config(
            environment={"dev": {"transcript": BindingValue(model="haiku")}},
            show={"clarity_engine": {"transcript": BindingValue(model="haiku")}},
            user={"u1": {"transcript": BindingValue(model="sonnet")}},
        )
        with patch("core.llm_config.resolver.get_config", return_value=cfg), \
             patch("core.llm_config.resolver._env", return_value="dev"):
            bv = _resolve_binding("transcript", show="clarity_engine", user_id="u1")
        assert bv.model == "sonnet"

    def test_cohort_overrides_show(self):
        cfg = _test_config(
            show={"clarity_engine": {"transcript": BindingValue(model="haiku")}},
            cohort={"beta": {"transcript": BindingValue(model="sonnet")}},
        )
        with patch("core.llm_config.resolver.get_config", return_value=cfg), \
             patch("core.llm_config.resolver._env", return_value="dev"):
            bv = _resolve_binding("transcript", show="clarity_engine", cohort="beta")
        assert bv.model == "sonnet"

    def test_nonexistent_user_falls_through(self):
        cfg = _test_config()
        with patch("core.llm_config.resolver.get_config", return_value=cfg), \
             patch("core.llm_config.resolver._env", return_value="dev"):
            bv = _resolve_binding("transcript", user_id="unknown_user")
        assert bv.model == "sonnet"  # falls through to task default


# ---------------------------------------------------------------------------
# _resolve_speaker_binding
# ---------------------------------------------------------------------------


class TestResolveSpeakerBinding:
    def test_known_speaker(self):
        cfg = _test_config()
        with patch("core.llm_config.resolver.get_config", return_value=cfg):
            bv = _resolve_speaker_binding("kenji")
        assert bv.model == "edge-tts"
        assert bv.voice_id == "v1"

    def test_unknown_speaker_raises(self):
        cfg = _test_config()
        with patch("core.llm_config.resolver.get_config", return_value=cfg):
            with pytest.raises(ValueError, match="No speaker binding"):
                _resolve_speaker_binding("unknown_speaker")


# ---------------------------------------------------------------------------
# _provider_and_model
# ---------------------------------------------------------------------------


class TestProviderAndModel:
    def test_returns_pair(self):
        cfg = _test_config()
        with patch("core.llm_config.resolver.get_config", return_value=cfg):
            bv = BindingValue(model="sonnet")
            provider, model = _provider_and_model(bv)
        assert provider.type == "anthropic"
        assert model.model_id == "claude-sonnet-4-6"
