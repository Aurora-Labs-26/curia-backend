"""
tests/test_brief_store.py — TDD for brief/store.py (ported cache_service on
the shared core DB pool, Curia-identity users).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brief import store


class TestPureFunctions:
    def test_normalize_url(self):
        assert store.normalize_url("  HTTPS://Example.com/A/  ") == "https://example.com/a"
        assert store.normalize_url("https://x.com/path") == "https://x.com/path"

    def test_estimate_duration_150wpm(self):
        assert store.estimate_duration_s(150) == 60.0
        assert store.estimate_duration_s(0) == 0.0

    def test_placeholder_mp3_url(self):
        assert store.placeholder_mp3_url("segment", "abc") == "placeholder://segment/abc.mp3"

    def test_display_name_prefers_column(self):
        assert store.display_name_for({"display_name": "Arihant", "id": "x"}) == "Arihant"

    def test_display_name_falls_back(self):
        assert store.display_name_for({"display_name": "", "id": ""}) == "there"


def _pool_with(fetchrow=None):
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=fetchrow)
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    pool.fetch = AsyncMock(return_value=[])
    return pool


class TestIdentityContract:
    async def test_create_user_upserts_by_curia_text_id(self):
        pool = _pool_with(fetchrow={"id": "claude-e2e-test"})
        with patch.object(store.harness_db, "get_pool", AsyncMock(return_value=pool)):
            uid = await store.create_user("claude-e2e-test", "Claude", "Mumbai")
        assert uid == "claude-e2e-test"
        sql = pool.fetchrow.call_args[0][0]
        assert "ON CONFLICT (id)" in sql          # upsert keyed on Curia id, not email
        assert "email" not in sql
        args = pool.fetchrow.call_args[0][1:]
        assert args[0] == "claude-e2e-test" and args[1] == "Claude"

    async def test_put_cached_segment_nulls_faithfulness_memo(self):
        # text change must invalidate the memoized verdict (harness invariant)
        pool = _pool_with(fetchrow={"id": "cache-1"})
        with patch.object(store.harness_db, "get_pool", AsyncMock(return_value=pool)):
            await store.put_cached_segment(
                "a1", "standard", False, {"text": "hello"}, "placeholder://segment/x.mp3", 10.0
            )
        sql = pool.fetchrow.call_args[0][0]
        assert "faithfulness_severity" in sql and "NULL" in sql


class TestJsonbTolerance:
    """core/db's jsonb codec auto-decodes to dict/list — the ported reads must
    NOT json.loads again (prod failure: 'the JSON object must be str... not dict')."""

    def test_jsonb_passthrough_dict_list_str_none(self):
        assert store._jsonb({"a": 1}) == {"a": 1}
        assert store._jsonb([1, 2]) == [1, 2]
        assert store._jsonb('{"a": 1}') == {"a": 1}
        assert store._jsonb(None) is None

    async def test_get_cached_segment_accepts_decoded_jsonb(self):
        row = {"id": "c1", "transcript_json": {"segments": [{"text": "hi"}]},
               "mp3_url": None, "duration_s": 30, "faithfulness_passed": None,
               "faithfulness_score": None, "faithfulness_detail": None}
        pool = MagicMock(); pool.fetchrow = AsyncMock(return_value=row)
        with patch.object(store.harness_db, "get_pool", AsyncMock(return_value=pool)):
            out = await store.get_cached_segment("a1", "standard", False)
        assert out["transcript_json"] == {"segments": [{"text": "hi"}]}


# ---------------------------------------------------------------------------
# Pure helpers — full edge sweep (GAN round: each verified mutation-killing)
# ---------------------------------------------------------------------------


class TestPureHelperEdges:
    def test_normalize_url_case_whitespace_trailing_slash(self):
        assert store.normalize_url("  HTTPS://X.com/A/ ") == "https://x.com/a"
        assert store.normalize_url("https://x.com/a") == "https://x.com/a"

    def test_normalize_url_strips_only_one_trailing_slash(self):
        assert store.normalize_url("https://x.com//") == "https://x.com/"

    def test_estimate_duration_rounding_and_zero(self):
        assert store.estimate_duration_s(0) == 0.0
        assert store.estimate_duration_s(150) == 60.0
        assert store.estimate_duration_s(75, wpm=150) == 30.0
        assert store.estimate_duration_s(1) == 0.4          # round(0.4, 1)

    def test_to_time_accepts_time_and_strings(self):
        import datetime
        assert store._to_time("07:00") == datetime.time(7, 0)
        assert store._to_time("23:59:59") == datetime.time(23, 59)   # secs dropped
        t = datetime.time(5, 30)
        assert store._to_time(t) is t

    def test_to_date_rejects_garbage(self):
        with pytest.raises(ValueError):
            store._to_date("23-07-2026")

    def test_pg_deleted_count(self):
        assert store._pg_deleted_count("DELETE 7") == 7
        assert store._pg_deleted_count("DELETE 0") == 0

    def test_placeholder_simhash_case_and_punct_insensitive(self):
        a = store._placeholder_simhash("Fed Cuts Rates!")
        b = store._placeholder_simhash("fed cuts rates")
        assert a == b
        assert a != store._placeholder_simhash("fed hikes rates")

    def test_display_name_precedence(self):
        assert store.display_name_for({"display_name": "Ari", "id": "x"}) == "Ari"
        assert store.display_name_for({"display_name": "  ", "id": "kenji-77"}) == "Kenji"
        assert store.display_name_for({"display_name": "", "id": "a@b.com"}) == "A"


class TestGetOrCreateDailyBrief:
    def _pool(self, insert_row, select_row=None):
        conn = MagicMock()
        conn.fetchrow = AsyncMock(side_effect=[insert_row, select_row])
        pool = MagicMock()
        acq = MagicMock()
        acq.__aenter__ = AsyncMock(return_value=conn)
        acq.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=acq)
        return pool, conn

    async def test_fresh_insert_reports_created(self):
        pool, _ = self._pool({"id": "b1", "status": "generating"})
        with patch.object(store.harness_db, "get_pool", AsyncMock(return_value=pool)):
            out = await store.get_or_create_daily_brief("u1", "2026-07-23")
        assert out == {"id": "b1", "status": "generating", "created": True}

    async def test_conflict_falls_back_to_select_not_created(self):
        pool, conn = self._pool(None, {"id": "b1", "status": "ready"})
        with patch.object(store.harness_db, "get_pool", AsyncMock(return_value=pool)):
            out = await store.get_or_create_daily_brief("u1", "2026-07-23")
        assert out == {"id": "b1", "status": "ready", "created": False}
        assert conn.fetchrow.await_count == 2


class TestGetUserTopics:
    async def test_splits_chosen_and_custom(self):
        rows = [
            {"id": "t1", "name": "Tech", "beat": "Tech", "is_system": True, "type": "chosen"},
            {"id": "t2", "name": "chess", "beat": None, "is_system": False, "type": "custom"},
        ]
        pool = MagicMock(); pool.fetch = AsyncMock(return_value=rows)
        with patch.object(store.harness_db, "get_pool", AsyncMock(return_value=pool)):
            out = await store.get_user_topics("u1")
        assert [t["name"] for t in out["chosen"]] == ["Tech"]
        assert [t["name"] for t in out["custom"]] == ["chess"]
        assert "is_active = true" in pool.fetch.await_args.args[0]
