"""
tests/test_brief_faithfulness.py — the inline generate→judge→retry gate
(brief/faithfulness.py). LLM + pipeline mocked. Spec under test:
2 generation attempts max; retry only on critical/moderate; the 2nd attempt
ships regardless of its verdict; judge failures fail OPEN.
"""

from unittest.mock import AsyncMock, patch

import pytest

from brief import faithfulness
from brief.pipeline import ArticleSegmentRequest


def _req(**kw):
    defaults = dict(title="Fed cuts rates", full_text="The Fed cut rates by 25bp.",
                    segment_type="standard", content_fetched=True)
    defaults.update(kw)
    return ArticleSegmentRequest(**defaults)


def _seg(text="attempt one text", wc=100):
    return {"text": text, "word_count": wc, "latency_ms": 5, "model": None,
            "simulated": False, "input_tokens": None, "output_tokens": None}


CRITICAL = {"claims": [{"text": "invented 40% figure", "severity": "critical",
                        "explanation": "not in source"}]}
CLEAN = {"claims": []}


class TestJudgeSegment:
    async def test_clean_verdict(self):
        with patch.object(faithfulness, "call_llm", AsyncMock(return_value=CLEAN)):
            v = await faithfulness.judge_segment("t", "title", "source")
        assert v == {"severity": "none", "claims": []}

    async def test_worst_severity_wins(self):
        mixed = {"claims": [
            {"text": "a", "severity": "moderate", "explanation": ""},
            {"text": "b", "severity": "critical", "explanation": ""}]}
        with patch.object(faithfulness, "call_llm", AsyncMock(return_value=mixed)):
            v = await faithfulness.judge_segment("t", "title", "source")
        assert v["severity"] == "critical"
        assert len(v["claims"]) == 2

    async def test_judge_crash_fails_open_as_unknown(self):
        with patch.object(faithfulness, "call_llm",
                          AsyncMock(side_effect=RuntimeError("llm down"))):
            v = await faithfulness.judge_segment("t", "title", "source")
        assert v == {"severity": "unknown", "claims": []}

    async def test_malformed_claims_treated_as_clean(self):
        with patch.object(faithfulness, "call_llm",
                          AsyncMock(return_value={"claims": "not-a-list"})):
            v = await faithfulness.judge_segment("t", "title", "source")
        assert v["severity"] == "none"

    async def test_headline_only_marks_source_unavailable(self):
        judge = AsyncMock(return_value=CLEAN)
        with patch.object(faithfulness, "call_llm", judge):
            await faithfulness.judge_segment("t", "title", "", source_available=False)
        assert "UNAVAILABLE" in judge.await_args.args[1]


class TestGenerateVerifiedSegment:
    def _run(self, gen_results, verdicts):
        pm = AsyncMock()
        pm.run_article_segment_step = AsyncMock(side_effect=gen_results)
        judge = AsyncMock(side_effect=verdicts)
        return pm, judge

    async def test_clean_first_attempt_single_generation(self):
        pm, judge = self._run([_seg()], [{"severity": "none", "claims": []}])
        with patch("brief.pipeline.pipeline_manager", pm), \
             patch.object(faithfulness, "judge_segment", judge):
            out = await faithfulness.generate_verified_segment(_req())
        assert pm.run_article_segment_step.await_count == 1
        assert judge.await_count == 1
        assert out["text"] == "attempt one text"
        assert out["faithfulness"]["severity"] == "none"

    async def test_flagged_then_clean_ships_attempt_two(self):
        pm, judge = self._run(
            [_seg("attempt one text"), _seg("attempt two text")],
            [{"severity": "critical", "claims": CRITICAL["claims"]},
             {"severity": "none", "claims": []}])
        with patch("brief.pipeline.pipeline_manager", pm), \
             patch.object(faithfulness, "judge_segment", judge):
            out = await faithfulness.generate_verified_segment(_req())
        assert pm.run_article_segment_step.await_count == 2
        assert out["text"] == "attempt two text"
        assert out["faithfulness"]["severity"] == "none"

    async def test_retry_threads_prior_text_and_feedback(self):
        pm, judge = self._run(
            [_seg("attempt one text"), _seg("attempt two text")],
            [{"severity": "critical", "claims": CRITICAL["claims"]},
             {"severity": "none", "claims": []}])
        with patch("brief.pipeline.pipeline_manager", pm), \
             patch.object(faithfulness, "judge_segment", judge):
            await faithfulness.generate_verified_segment(_req())
        retry_req = pm.run_article_segment_step.await_args_list[1].args[0]
        assert retry_req.prior_text == "attempt one text"
        assert "invented 40% figure" in retry_req.prior_feedback

    async def test_double_flag_ships_attempt_two_with_honest_verdict(self):
        pm, judge = self._run(
            [_seg("attempt one text"), _seg("attempt two text")],
            [{"severity": "moderate", "claims": [{"text": "x", "severity": "moderate", "explanation": ""}]},
             {"severity": "critical", "claims": CRITICAL["claims"]}])
        with patch("brief.pipeline.pipeline_manager", pm), \
             patch.object(faithfulness, "judge_segment", judge):
            out = await faithfulness.generate_verified_segment(_req())
        assert pm.run_article_segment_step.await_count == 2   # hard cap: 2 attempts
        assert out["text"] == "attempt two text"              # 2nd ships regardless
        assert out["faithfulness"]["severity"] == "critical"  # verdict stays honest

    async def test_unknown_verdict_never_triggers_retry(self):
        pm, judge = self._run([_seg()], [{"severity": "unknown", "claims": []}])
        with patch("brief.pipeline.pipeline_manager", pm), \
             patch.object(faithfulness, "judge_segment", judge):
            out = await faithfulness.generate_verified_segment(_req())
        assert pm.run_article_segment_step.await_count == 1
        assert out["faithfulness"]["severity"] == "unknown"


class TestMemoizationWiring:
    """The runner must memoize the verdict AFTER put_cached_segment (put nulls
    the memo columns) and skip memoization on unknown verdicts."""

    async def test_runner_memoizes_after_put(self):
        from tests.test_brief_runner import Rig
        rig = Rig()
        rig.regen.generate_verified_segment = AsyncMock(return_value={
            "text": "fresh text", "word_count": 100,
            "faithfulness": {"severity": "moderate", "claims": [{"text": "c"}]}})
        await rig.run()
        assert rig.cache.set_cached_segment_faithfulness.await_count == 5
        first = rig.cache.set_cached_segment_faithfulness.await_args_list[0].args
        assert first[3] == "moderate"
        # order: every memo write comes after the corresponding put
        assert rig.cache.put_cached_segment.await_count == 5

    async def test_runner_skips_memo_on_unknown(self):
        from tests.test_brief_runner import Rig
        rig = Rig()
        rig.regen.generate_verified_segment = AsyncMock(return_value={
            "text": "fresh text", "word_count": 100,
            "faithfulness": {"severity": "unknown", "claims": []}})
        await rig.run()
        rig.cache.set_cached_segment_faithfulness.assert_not_awaited()

    async def test_runner_tolerates_missing_faithfulness_key(self):
        from tests.test_brief_runner import Rig
        rig = Rig()   # default regen mock has no "faithfulness" key
        result = await rig.run()
        assert result["status"] == "ready"
        rig.cache.set_cached_segment_faithfulness.assert_not_awaited()


class TestConfig:
    def test_judge_step_registered_and_bound(self):
        from brief.llm import BRIEF_STEPS
        from core.llm_config.resolver import reload_config
        assert "judge" in BRIEF_STEPS
        cfg = reload_config()
        assert cfg.bindings.task["brief.judge"].model is not None


class TestHeadlineOnlyMode:
    def _pm(self, gens):
        pm = AsyncMock()
        pm.run_article_segment_step = AsyncMock(side_effect=gens)
        return pm

    async def test_headline_only_judges_once_never_retries(self):
        pm = self._pm([_seg("headline segment")])
        judge = AsyncMock(return_value={"severity": "critical", "claims": CRITICAL["claims"]})
        with patch("brief.pipeline.pipeline_manager", pm), \
             patch.object(faithfulness, "judge_segment", judge):
            out = await faithfulness.generate_verified_segment(_req(content_fetched=False))
        assert pm.run_article_segment_step.await_count == 1     # no retry without source
        assert judge.await_count == 1
        assert out["faithfulness"]["severity"] == "critical"    # verdict still honest

    async def test_unverifiable_never_triggers_retry_with_source(self):
        pm = self._pm([_seg()])
        judge = AsyncMock(return_value={"severity": "unverifiable",
                                        "claims": [{"text": "x", "severity": "unverifiable",
                                                    "explanation": ""}]})
        with patch("brief.pipeline.pipeline_manager", pm), \
             patch.object(faithfulness, "judge_segment", judge):
            out = await faithfulness.generate_verified_segment(_req())
        assert pm.run_article_segment_step.await_count == 1
        assert out["faithfulness"]["severity"] == "unverifiable"

    async def test_unverifiable_ranks_below_moderate(self):
        claims = [{"text": "a", "severity": "unverifiable", "explanation": ""},
                  {"text": "b", "severity": "moderate", "explanation": ""}]
        assert faithfulness._worst_severity(claims) == "moderate"

    def test_prompt_teaches_unverifiable_for_headline_only(self):
        assert "unverifiable" in faithfulness.SYSTEM_FAITHFULNESS_JUDGE_PROMPT
        assert "CONTRADICT" in faithfulness.SYSTEM_FAITHFULNESS_JUDGE_PROMPT
