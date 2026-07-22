"""
tests/test_brief_llm.py — TDD for brief/llm.py (daily-brief LLM layer ported
onto core/llm_config; replaces the harness's hardcoded-model llm_service).

Contract:
  call_llm(system, user, step, json_response=True) routes step →
  models.yaml task binding `brief.<step>`; no hardcoded model IDs, no mock
  mode, no per-request API-key override.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from core.llm_config.resolver import reload_config


class TestBindings:
    def test_models_yaml_has_brief_bindings(self):
        cfg = reload_config()
        tasks = cfg.bindings.task
        for t in ("brief.score_curate", "brief.segment", "brief.bookends"):
            assert t in tasks, f"missing binding {t} in config/models.yaml"

    def test_score_curate_is_strong_tier(self):
        # documented live failure: haiku couldn't collapse 7 wire-service dupes
        cfg = reload_config()
        def model_of(task):
            b = cfg.bindings.task[task]
            return getattr(b, "model", b) if not isinstance(b, str) else b
        assert "sonnet" in model_of("brief.score_curate")
        assert "haiku" in model_of("brief.segment")
        assert "haiku" in model_of("brief.bookends")


class TestCallLLM:
    def _fake_lm(self, response_text):
        lm = MagicMock(return_value=[response_text])
        return lm

    async def test_routes_step_to_binding(self):
        from brief import llm as brief_llm
        lm = self._fake_lm('{"ok": true}')
        with patch.object(brief_llm, "_resolve_lm", return_value=lm) as res:
            out = await brief_llm.call_llm("sys", "user", step="segment")
        res.assert_called_once_with("brief.segment")
        assert out == {"ok": True}

    async def test_messages_carry_system_and_user(self):
        from brief import llm as brief_llm
        lm = self._fake_lm('{"ok": 1}')
        with patch.object(brief_llm, "_resolve_lm", return_value=lm):
            await brief_llm.call_llm("SYSTEM PROMPT", "USER CONTENT", step="score_curate")
        messages = lm.call_args.kwargs["messages"]
        assert messages[0] == {"role": "system", "content": "SYSTEM PROMPT"}
        assert messages[1] == {"role": "user", "content": "USER CONTENT"}

    async def test_json_fences_stripped(self):
        from brief import llm as brief_llm
        lm = self._fake_lm('```json\n{"a": 2}\n```')
        with patch.object(brief_llm, "_resolve_lm", return_value=lm):
            assert await brief_llm.call_llm("s", "u", step="bookends") == {"a": 2}

    async def test_text_mode(self):
        from brief import llm as brief_llm
        lm = self._fake_lm("plain segment text")
        with patch.object(brief_llm, "_resolve_lm", return_value=lm):
            out = await brief_llm.call_llm("s", "u", step="segment", json_response=False)
        assert out == "plain segment text"

    async def test_unknown_step_raises(self):
        from brief import llm as brief_llm
        with pytest.raises(ValueError, match="unknown brief step"):
            await brief_llm.call_llm("s", "u", step="faithfulness_judge")  # parked with eval

    async def test_bad_json_raises_with_context(self):
        from brief import llm as brief_llm
        lm = self._fake_lm("not json at all")
        with patch.object(brief_llm, "_resolve_lm", return_value=lm):
            with pytest.raises(ValueError, match="JSON"):
                await brief_llm.call_llm("s", "u", step="segment")

    def test_no_hardcoded_models_in_module(self):
        import inspect
        from brief import llm as brief_llm
        src = inspect.getsource(brief_llm)
        assert "claude-" not in src, "hardcoded model ID found — use models.yaml bindings"
        assert "AsyncAnthropic" not in src, "direct SDK client found — use core/llm_config"
