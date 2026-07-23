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
