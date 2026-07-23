"""
tests/test_brief_runner.py — assembly-logic tests for
brief/user_brief_runner.generate_brief_for_user with every collaborator mocked
(store, pipeline, news, weather, parked). Written GAN-style: each behavior
below was verified to FAIL under a targeted source mutation before landing.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brief import store
from brief import user_brief_runner as runner


# ---------------------------------------------------------------------------
# Harness — a fully wired happy-path mock set each test can tweak
# ---------------------------------------------------------------------------


def _selection(title, slot="Front Page", url=None, reason="because"):
    return {"title": title, "slot": slot, "url": url or f"https://x.com/{title}",
            "reason": reason, "source": "src"}


class Rig:
    def __init__(self, n_standard_pool=3, with_local=True, cached_urls=()):
        self.cache = MagicMock()
        self.pipeline = MagicMock()
        self.ns = MagicMock()
        self.weather = MagicMock()
        self.regen = MagicMock()

        # pure functions stay real — assembly math must not be mocked away
        self.cache.estimate_duration_s = store.estimate_duration_s
        self.cache.placeholder_mp3_url = store.placeholder_mp3_url
        self.cache.display_name_for = store.display_name_for

        self.cache.get_or_create_daily_brief = AsyncMock(
            return_value={"id": "b1", "status": "generating", "created": True})
        self.cache.get_user = AsyncMock(return_value={
            "id": "u1", "display_name": "Ari", "location_name": "Mumbai, India"})
        self.cache.get_user_topics = AsyncMock(return_value={
            "chosen": [{"id": "t1", "name": "Tech", "beat": "Tech", "type": "chosen"}],
            "custom": [{"id": "t9", "name": "chess", "beat": None, "type": "custom"}]})
        self.cache.get_preopt_candidates = AsyncMock(return_value=[
            {"title": f"pre {i}", "url": f"https://pre/{i}", "topic_id": "t1"}
            for i in range(5)])
        self._article_ids = {}
        async def _upsert(url="", title=""):
            return self._article_ids.setdefault(url, f"a{len(self._article_ids)}")
        self.cache.upsert_article = AsyncMock(side_effect=_upsert)

        self.cached_urls = set(cached_urls)
        async def _get_cached(article_id, segment_type, is_local):
            url = next((u for u, a in self._article_ids.items() if a == article_id), "")
            if url in self.cached_urls:
                return {"id": f"c-{article_id}", "mp3_url": "placeholder://old.mp3",
                        "duration_s": 30.0,
                        "transcript_json": {"text": f"cached text {article_id}",
                                            "word_count": 75}}
            # second lookup (persistence step) sees the row put_cached_segment wrote
            if article_id in self._put:
                return {"id": f"c-{article_id}", **self._put[article_id]}
            return None
        self.cache.get_cached_segment = AsyncMock(side_effect=_get_cached)
        self._put = {}
        async def _put_cached(article_id, segment_type, is_local, transcript_json,
                              mp3_url, duration_s):
            self._put[article_id] = {"transcript_json": transcript_json,
                                     "mp3_url": mp3_url, "duration_s": duration_s}
        self.cache.put_cached_segment = AsyncMock(side_effect=_put_cached)
        self.cache.add_daily_brief_article = AsyncMock()
        self.cache.set_daily_brief_urls = AsyncMock()
        self.cache.save_transcript_record = AsyncMock()
        self.cache.mark_daily_brief_failed = AsyncMock()

        self.weather.get_weather_and_local_time = AsyncMock(
            return_value=("Sunny, 30°C", "9:00 AM"))

        self.ns.resolve_local_geo = MagicMock(return_value=("IN", "en-IN", "IN:en"))
        self.ns.fetch_articles_for_topic = MagicMock(return_value=[])

        sels = [_selection(f"story {i}") for i in range(n_standard_pool + 1)]
        if with_local:
            sels.append(_selection("mumbai story", slot="Local Pulse"))
        self.selections = sels

        async def _rank(req):
            arts = [a.model_dump() for a in req.articles]
            return {"ranked": [{**a, "included": True} for a in arts]}
        self.pipeline.run_rank_step = AsyncMock(side_effect=_rank)
        self.pipeline.run_score_curate_step = AsyncMock(return_value={
            "parsed": {"selections": self.selections,
                       "ranked_order": [], "local_ranked_order": []}})
        async def _fetch(req):
            return {"selections": [
                {**dict(s), "full_text": "body text", "content_fetched": True}
                for s in req.selections]}
        self.pipeline.run_content_fetch_step = AsyncMock(side_effect=_fetch)
        self.pipeline.run_intro_outro_step = AsyncMock(return_value={
            "intro": "hello there listener", "outro": "goodbye now"})

        self.regen.generate_verified_segment = AsyncMock(return_value={
            "text": "fresh segment text " * 10, "word_count": 150})
        self.regen.verify_cached_segment = AsyncMock(return_value=None)

    def patches(self):
        noop = MagicMock()
        noop.start_eval_run = AsyncMock(return_value="run1")
        for name in ("finish_eval_run", "run_checks_for_run", "log_fetched_articles",
                     "log_ranking_output", "log_llm_ranking_output",
                     "log_segment_transcript", "log_meta_segments",
                     "update_fetched_article_content"):
            setattr(noop, name, AsyncMock())
        noop.maybe_run_automated_judging = MagicMock()
        return [
            patch.object(runner, "cache_service", self.cache),
            patch.object(runner, "pipeline_manager", self.pipeline),
            patch.object(runner, "ns", self.ns),
            patch.object(runner, "weather_service", self.weather),
            patch.object(runner, "faithfulness_regen_service", self.regen),
            patch.object(runner, "eval_logging_service", noop),
            patch.object(runner, "eval_checks_service", noop),
            patch.object(runner, "automated_judging", noop),
        ]

    async def run(self):
        ps = self.patches()
        for p in ps:
            p.start()
        try:
            return await runner.generate_brief_for_user("u1", "2026-07-23")
        finally:
            for p in ps:
                p.stop()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    async def test_manifest_order_and_shape(self):
        result = await Rig().run()
        kinds = [m["kind"] for m in result["segments"]]
        assert kinds == ["intro", "lead", "standard", "standard", "standard",
                         "local", "outro"]
        assert result["status"] == "ready"
        assert result["brief_id"] == "b1"

    async def test_lead_is_first_nonlocal_selection(self):
        rig = Rig()
        result = await rig.run()
        lead = next(m for m in result["segments"] if m["kind"] == "lead")
        assert lead["title"] == "story 0"

    async def test_standard_ranks_are_1_2_3(self):
        rig = Rig()
        await rig.run()
        by_type = {}
        for c in rig.cache.add_daily_brief_article.await_args_list:
            k = c.kwargs
            by_type.setdefault(k["segment_type"], []).append(k["rank"])
        assert by_type["standard"] == [1, 2, 3]
        assert by_type["lead"] == [1]
        assert by_type["local"] == [1]

    async def test_brief_marked_ready_with_placeholder_urls(self):
        rig = Rig()
        await rig.run()
        k = rig.cache.set_daily_brief_urls.await_args.kwargs
        assert k["status"] == "ready"
        assert k["stitched_mp3_url"].startswith("placeholder://stitched/")

    async def test_total_duration_is_sum_of_segment_durations(self):
        result = await Rig().run()
        assert result["total_duration_s"] == pytest.approx(
            round(sum(m["duration_s"] for m in result["segments"]), 1))

    async def test_all_misses_generate_and_cache_five_segments(self):
        rig = Rig()
        result = await rig.run()
        assert rig.regen.generate_verified_segment.await_count == 5
        assert rig.cache.put_cached_segment.await_count == 5
        assert result["cache_summary"] == {"hits": 0, "misses": 5}

    async def test_transcript_record_saved_with_manifest(self):
        rig = Rig()
        result = await rig.run()
        saved = rig.cache.save_transcript_record.await_args.kwargs
        assert saved["segments"] == result["segments"]
        assert saved["user_id"] == "u1"

    async def test_intro_outro_gets_user_context(self):
        rig = Rig()
        await rig.run()
        req = rig.pipeline.run_intro_outro_step.await_args.args[0]
        assert req.display_name == "Ari"
        assert req.weather == "Sunny, 30°C"
        assert len(req.selections) == 5


# ---------------------------------------------------------------------------
# Cache-hit behavior
# ---------------------------------------------------------------------------


class TestCacheHits:
    async def test_full_hit_skips_fetch_and_generation(self):
        rig = Rig()
        rig.cached_urls = {s["url"] for s in rig.selections}
        result = await rig.run()
        rig.pipeline.run_content_fetch_step.assert_not_awaited()
        rig.regen.generate_verified_segment.assert_not_awaited()
        rig.cache.put_cached_segment.assert_not_awaited()
        assert result["cache_summary"] == {"hits": 5, "misses": 0}

    async def test_partial_hit_fetches_only_misses(self):
        rig = Rig()
        rig.cached_urls = {rig.selections[0]["url"], rig.selections[1]["url"]}
        result = await rig.run()
        fetched = rig.pipeline.run_content_fetch_step.await_args.args[0].selections
        assert len(fetched) == 3
        assert result["cache_summary"] == {"hits": 2, "misses": 3}

    async def test_cached_text_reused_verbatim_in_manifest(self):
        rig = Rig()
        rig.cached_urls = {rig.selections[0]["url"]}
        result = await rig.run()
        lead = next(m for m in result["segments"] if m["kind"] == "lead")
        assert lead["text"].startswith("cached text ")
        assert lead["cache_hit"] is True


# ---------------------------------------------------------------------------
# Selection edge cases
# ---------------------------------------------------------------------------


class TestSelectionEdges:
    async def test_more_than_four_nonlocal_selections_truncated(self):
        rig = Rig(n_standard_pool=6)          # 7 non-local + 1 local offered
        result = await rig.run()
        kinds = [m["kind"] for m in result["segments"]]
        assert kinds.count("standard") == 3 and kinds.count("lead") == 1
        assert rig.cache.add_daily_brief_article.await_count == 5

    async def test_no_local_selection_yields_four_articles(self):
        rig = Rig(with_local=False)
        result = await rig.run()
        kinds = [m["kind"] for m in result["segments"]]
        assert "local" not in kinds
        assert kinds == ["intro", "lead", "standard", "standard", "standard", "outro"]

    async def test_local_slot_always_last_even_if_listed_first(self):
        rig = Rig()
        rig.selections.insert(0, rig.selections.pop())      # local first in LLM output
        rig.pipeline.run_score_curate_step = AsyncMock(return_value={
            "parsed": {"selections": rig.selections,
                       "ranked_order": [], "local_ranked_order": []}})
        result = await rig.run()
        kinds = [m["kind"] for m in result["segments"]]
        assert kinds[-2] == "local"          # before outro, after the standards

    async def test_only_included_articles_reach_score_step(self):
        rig = Rig()
        async def _rank(req):
            arts = [a.model_dump() for a in req.articles]
            return {"ranked": [{**a, "included": i % 2 == 0}
                               for i, a in enumerate(arts)]}
        rig.pipeline.run_rank_step = AsyncMock(side_effect=_rank)
        await rig.run()
        sent = rig.pipeline.run_score_curate_step.await_args.args[0].articles
        assert 0 < len(sent) < 5              # strictly the included subset


# ---------------------------------------------------------------------------
# Pool building — local query construction
# ---------------------------------------------------------------------------


class TestLocalQuery:
    async def test_local_query_ands_location_with_or_topics(self):
        rig = Rig()
        await rig.run()
        local_call = [c for c in rig.ns.fetch_articles_for_topic.call_args_list
                      if str(c.args[-1]).startswith("Local:")][0]
        q = local_call.args[0]
        assert q == '"Mumbai, India" ("Tech" OR "chess")'

    async def test_local_query_bare_location_when_no_topics(self):
        rig = Rig()
        rig.cache.get_user_topics = AsyncMock(return_value={"chosen": [], "custom": []})
        await rig.run()
        local_call = [c for c in rig.ns.fetch_articles_for_topic.call_args_list
                      if str(c.args[-1]).startswith("Local:")][0]
        assert local_call.args[0] == '"Mumbai, India"'

    async def test_custom_topics_fetched_individually_and_quoted(self):
        rig = Rig()
        await rig.run()
        custom_calls = [c for c in rig.ns.fetch_articles_for_topic.call_args_list
                        if str(c.args[-1]).startswith("Custom:")]
        assert [c.args[0] for c in custom_calls] == ['"chess"']


# ---------------------------------------------------------------------------
# Failure + short-circuit paths
# ---------------------------------------------------------------------------


class TestFailurePaths:
    async def test_unknown_user_marks_failed_and_raises(self):
        rig = Rig()
        rig.cache.get_user = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Unknown user_id"):
            await rig.run()
        rig.cache.mark_daily_brief_failed.assert_awaited_once_with("b1")
        rig.cache.set_daily_brief_urls.assert_not_awaited()

    async def test_score_step_crash_marks_failed_and_propagates(self):
        rig = Rig()
        rig.pipeline.run_score_curate_step = AsyncMock(
            side_effect=RuntimeError("llm down"))
        with pytest.raises(RuntimeError, match="llm down"):
            await rig.run()
        rig.cache.mark_daily_brief_failed.assert_awaited_once_with("b1")

    async def test_existing_ready_brief_short_circuits_to_bookends(self):
        rig = Rig()
        rig.cache.get_or_create_daily_brief = AsyncMock(
            return_value={"id": "b1", "status": "ready", "created": False})
        with patch.object(runner, "_regenerate_bookends_for_brief",
                          AsyncMock(return_value={"status": "ready", "regen": True})) as re:
            result = await rig.run()
        re.assert_awaited_once()
        assert result == {"status": "ready", "regen": True}
        rig.pipeline.run_rank_step.assert_not_awaited()      # zero re-ranking

    async def test_existing_failed_brief_regenerates_fully(self):
        rig = Rig()
        rig.cache.get_or_create_daily_brief = AsyncMock(
            return_value={"id": "b1", "status": "failed", "created": False})
        result = await rig.run()
        assert result["status"] == "ready"
        rig.pipeline.run_rank_step.assert_awaited_once()


# ---------------------------------------------------------------------------
# Bookend regeneration path
# ---------------------------------------------------------------------------


class TestRegenerateBookends:
    async def test_regen_reuses_articles_and_never_touches_status(self):
        rig = Rig()
        rig.cache.get_daily_brief_detail = AsyncMock(return_value={
            "brief": {"id": "b1", "status": "ready"},
            "articles": [
                {"title": "t1", "reason": "r", "segment_type": "lead",
                 "article_id": "a1",
                 "transcript_json": {"text": "lead text", "word_count": 2},
                 "mp3_url": "m", "duration_s": 10, "cache_hit": True},
            ]})
        rig.cache.get_user_topics = AsyncMock(return_value={"chosen": [], "custom": []})
        ps = rig.patches()
        for p in ps:
            p.start()
        try:
            result = await runner._regenerate_bookends_for_brief("b1", "u1", "2026-07-23")
        finally:
            for p in ps:
                p.stop()
        assert result["status"] == "ready"
        assert result["brief_id"] == "b1"
        assert result["segments"][0]["kind"] == "intro"
        assert result["segments"][-1]["kind"] == "outro"
        rig.pipeline.run_rank_step.assert_not_awaited()
        rig.regen.generate_verified_segment.assert_not_awaited()
        rig.cache.set_daily_brief_urls.assert_not_awaited()
        rig.cache.mark_daily_brief_failed.assert_not_awaited()
