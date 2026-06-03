"""
tests/test_config_loader.py
Tests for core/llm_config/loader.py — config path resolution and YAML loading.

Verifies:
  - config_path returns default when env var unset
  - config_path respects CURIA_LLM_CONFIG env var
  - load_config returns CuriaConfig with providers, models, bindings
  - load_config raises FileNotFoundError for missing file
  - load_config raises ValueError for invalid YAML
"""

from pathlib import Path
from unittest.mock import patch

import pytest


class TestConfigPath:

    def test_default_path(self, monkeypatch):
        monkeypatch.delenv("CURIA_LLM_CONFIG", raising=False)
        from core.llm_config.loader import config_path
        result = config_path()
        assert result.name == "models.yaml"
        assert "config" in str(result)

    def test_env_override(self, monkeypatch, tmp_path):
        custom = tmp_path / "custom.yaml"
        custom.touch()
        monkeypatch.setenv("CURIA_LLM_CONFIG", str(custom))
        from core.llm_config.loader import config_path
        assert config_path() == custom


class TestLoadConfig:

    def test_loads_real_config(self):
        from core.llm_config.loader import load_config
        cfg = load_config()
        assert hasattr(cfg, "providers")
        assert hasattr(cfg, "models")
        assert hasattr(cfg, "bindings")
        assert len(cfg.providers) > 0
        assert len(cfg.models) > 0

    def test_has_expected_providers(self):
        from core.llm_config.loader import load_config
        cfg = load_config()
        provider_names = set(cfg.providers.keys())
        assert "anthropic" in provider_names
        assert "hume" in provider_names

    def test_has_tts_models(self):
        from core.llm_config.loader import load_config
        cfg = load_config()
        tts_models = [alias for alias, m in cfg.models.items() if m.kind == "tts"]
        assert len(tts_models) >= 1

    def test_has_speaker_bindings(self):
        from core.llm_config.loader import load_config
        cfg = load_config()
        assert len(cfg.bindings.speaker) > 0
        for name, binding in cfg.bindings.speaker.items():
            assert binding.voice_id is not None

    def test_missing_file_raises(self, tmp_path):
        from core.llm_config.loader import load_config
        missing = tmp_path / "nope.yaml"
        with patch("core.llm_config.loader.config_path", return_value=missing):
            with pytest.raises(FileNotFoundError):
                load_config()

    def test_invalid_yaml_raises(self, tmp_path):
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("not: [valid: yaml: {broken")
        from core.llm_config.loader import load_config
        with patch("core.llm_config.loader.config_path", return_value=bad_yaml):
            with pytest.raises(Exception):
                load_config()
