"""
tests/test_schema_config.py
Unit tests for core/llm_config/schema.py — Pydantic config validation.
Pure model validation, no I/O.
"""

import pytest

from core.llm_config.schema import (
    BindingValue,
    Bindings,
    CuriaConfig,
    ModelConfig,
    ProviderConfig,
    _coerce_binding,
)


# ---------------------------------------------------------------------------
# _coerce_binding
# ---------------------------------------------------------------------------


class TestCoerceBinding:
    def test_string_to_binding(self):
        bv = _coerce_binding("sonnet-4-6")
        assert isinstance(bv, BindingValue)
        assert bv.model == "sonnet-4-6"
        assert bv.overrides == {}
        assert bv.voice_id is None

    def test_dict_to_binding(self):
        bv = _coerce_binding({"model": "opus-4-6", "overrides": {"temperature": 0.9}})
        assert bv.model == "opus-4-6"
        assert bv.overrides["temperature"] == 0.9

    def test_dict_with_voice_id(self):
        bv = _coerce_binding({"model": "edge-tts", "voice_id": "v-123"})
        assert bv.voice_id == "v-123"

    def test_passthrough_binding_value(self):
        orig = BindingValue(model="x")
        assert _coerce_binding(orig) is orig

    def test_invalid_type_raises(self):
        with pytest.raises(TypeError, match="Cannot coerce"):
            _coerce_binding(42)


# ---------------------------------------------------------------------------
# Bindings._normalize_short_form
# ---------------------------------------------------------------------------


class TestBindingsNormalize:
    def test_task_string_coerced(self):
        b = Bindings(task={"transcript": "sonnet-4-6"})
        assert b.task["transcript"].model == "sonnet-4-6"

    def test_speaker_string_coerced(self):
        b = Bindings(speaker={"kenji": {"model": "edge-tts", "voice_id": "v1"}})
        assert b.speaker["kenji"].model == "edge-tts"
        assert b.speaker["kenji"].voice_id == "v1"

    def test_nested_environment_coerced(self):
        b = Bindings(environment={"dev": {"transcript": "sonnet-4-6"}})
        assert b.environment["dev"]["transcript"].model == "sonnet-4-6"

    def test_nested_show_coerced(self):
        b = Bindings(show={"clarity": {"transcript": "sonnet-4-6"}})
        assert b.show["clarity"]["transcript"].model == "sonnet-4-6"

    def test_empty_bindings(self):
        b = Bindings()
        assert b.task == {}
        assert b.speaker == {}


# ---------------------------------------------------------------------------
# CuriaConfig._validate_references
# ---------------------------------------------------------------------------


def _minimal_config(**overrides):
    """Build a minimal valid CuriaConfig dict for testing."""
    base = {
        "providers": {
            "anthropic": {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
            "edge": {"type": "edge_tts", "api_key_env": "EDGE_TTS_KEY"},
        },
        "models": {
            "sonnet": {"provider": "anthropic", "model_id": "claude-sonnet-4-6", "kind": "llm"},
            "edge-tts": {"provider": "edge", "model_id": "edge-tts-1", "kind": "tts"},
        },
        "bindings": {
            "task": {"transcript": "sonnet"},
            "speaker": {"kenji": {"model": "edge-tts", "voice_id": "v1"}},
        },
    }
    base.update(overrides)
    return base


class TestCuriaConfigValidation:
    def test_valid_config_parses(self):
        cfg = CuriaConfig(**_minimal_config())
        assert "sonnet" in cfg.models
        assert cfg.bindings.task["transcript"].model == "sonnet"

    def test_model_unknown_provider_raises(self):
        data = _minimal_config()
        data["models"]["bad"] = {"provider": "unknown_provider", "model_id": "x", "kind": "llm"}
        with pytest.raises(ValueError, match="unknown provider"):
            CuriaConfig(**data)

    def test_binding_unknown_model_raises(self):
        data = _minimal_config()
        data["bindings"]["task"]["summary"] = "nonexistent-model"
        with pytest.raises(ValueError, match="unknown model"):
            CuriaConfig(**data)

    def test_speaker_missing_voice_id_raises(self):
        data = _minimal_config()
        data["bindings"]["speaker"]["arjun"] = {"model": "edge-tts"}
        with pytest.raises(ValueError, match="voice_id"):
            CuriaConfig(**data)

    def test_speaker_non_tts_model_raises(self):
        data = _minimal_config()
        data["bindings"]["speaker"]["arjun"] = {"model": "sonnet", "voice_id": "v2"}
        with pytest.raises(ValueError, match="expected 'tts'"):
            CuriaConfig(**data)

    def test_environment_binding_validated(self):
        data = _minimal_config()
        data["bindings"]["environment"] = {"dev": {"transcript": "nonexistent"}}
        with pytest.raises(ValueError, match="unknown model"):
            CuriaConfig(**data)

    def test_show_binding_validated(self):
        data = _minimal_config()
        data["bindings"]["show"] = {"clarity": {"transcript": "nonexistent"}}
        with pytest.raises(ValueError, match="unknown model"):
            CuriaConfig(**data)

    def test_cohort_binding_validated(self):
        data = _minimal_config()
        data["bindings"]["cohort"] = {"beta": {"transcript": "nonexistent"}}
        with pytest.raises(ValueError, match="unknown model"):
            CuriaConfig(**data)

    def test_user_binding_validated(self):
        data = _minimal_config()
        data["bindings"]["user"] = {"u1": {"transcript": "nonexistent"}}
        with pytest.raises(ValueError, match="unknown model"):
            CuriaConfig(**data)
