"""
tests/test_llm_config.py
LLM config layer — load YAML, validate schema, walk the resolver scope hierarchy.
No actual LLM calls.
"""

import pytest

from core.llm_config import get_config, reload_config
from core.llm_config.schema import CuriaConfig


def test_config_loads_and_validates():
    cfg = reload_config()  # force fresh read
    assert isinstance(cfg, CuriaConfig)
    assert "anthropic" in cfg.providers
    assert "voyage" in cfg.providers
    assert "elevenlabs" in cfg.providers


def test_known_models_exist():
    cfg = get_config()
    assert "haiku-4-5" in cfg.models
    assert "sonnet-4-6" in cfg.models
    assert "voyage-3" in cfg.models
    assert "eleven-multi-v2" in cfg.models


def test_required_task_bindings_exist():
    cfg = get_config()
    bindings = cfg.bindings.task
    required = {
        "transformation.summary",
        "transformation.metadata",
        "transformation.key_insights",
        "transformation.human_stakes",
        "transformation.core_tensions",
        "transformation.counterpoints",
        "transformation.examples",
        "idea_evaluation.batch",
        "idea_evaluation.single",
        "outline",
        "transcript",
        "embedding",
        "judge",
    }
    missing = required - set(bindings)
    assert not missing, f"missing task bindings in config/models.yaml: {missing}"


def test_speaker_bindings_have_voice_ids():
    cfg = get_config()
    for spk_name, binding in cfg.bindings.speaker.items():
        assert binding.voice_id, f"speaker '{spk_name}' missing voice_id"
        assert binding.model in cfg.models, f"speaker '{spk_name}' references unknown model"
        assert cfg.models[binding.model].kind == "tts", (
            f"speaker '{spk_name}' references model '{binding.model}' "
            f"which is kind={cfg.models[binding.model].kind}, expected tts"
        )


def test_resolver_walks_scope_hierarchy_for_known_task():
    """Resolver should at least find the task default for 'outline' without crashing.
    LLM construction will fail without ANTHROPIC_API_KEY — that's a separate concern."""
    from core.llm_config.resolver import _resolve_binding

    binding = _resolve_binding("outline")
    assert binding.model == "haiku-4-5"


def test_resolver_raises_on_unknown_task():
    from core.llm_config.resolver import _resolve_binding

    with pytest.raises(ValueError, match="No binding for task"):
        _resolve_binding("definitely_not_a_real_task")
