"""
tests/test_brief_store_prefs.py — brief/store.py's preference-replace semantics
and playback-progress writes. harness_db.get_pool() is mocked; no real DB.
"""

from unittest.mock import AsyncMock, patch

import pytest

from brief import store


def _fake_pool(fetch_rows=None, fetchrow_result=None, execute_result="UPDATE 1"):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=fetch_rows or [])
    pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    pool.execute = AsyncMock(return_value=execute_result)
    return pool


class TestCreateUserWithTopicsReplaces:
    """create_user_with_topics is the PUT /brief/preferences entry point — the
    submitted topic set must be the whole truth. link_user_topic alone is
    additive-only (ON CONFLICT DO NOTHING), so without an explicit prune,
    resaving prefs with fewer topics silently kept the old ones — a user could
    never deselect a beat. Regression coverage for that bug."""

    async def test_resave_with_fewer_topics_prunes_the_rest(self):
        system_topics = [
            {"id": "tech-id", "name": "Tech"},
            {"id": "biz-id", "name": "Business"},
        ]
        pool = _fake_pool()
        with patch.object(store, "harness_db") as db, \
             patch.object(store, "create_user", AsyncMock(return_value="u1")), \
             patch.object(store, "list_system_topics", AsyncMock(return_value=system_topics)), \
             patch.object(store, "link_user_topic", AsyncMock()) as link:
            db.get_pool = AsyncMock(return_value=pool)
            await store.create_user_with_topics(
                "u1", "Aditya", "Pune", "09:00", ["Tech"], [], "Asia/Kolkata",
            )

        link.assert_awaited_once_with("u1", "tech-id", "chosen")
        delete_call = pool.execute.await_args
        assert "DELETE FROM harness.user_topics" in delete_call.args[0]
        assert delete_call.args[1] == "u1"
        assert delete_call.args[2] == ["tech-id"]  # kept id excluded from the prune

    async def test_unknown_chosen_topic_name_is_skipped_not_kept(self):
        """A name that doesn't match any system topic must not end up in the
        keep-list either — otherwise it'd silently protect a stale link
        instead of just being ignored."""
        pool = _fake_pool()
        with patch.object(store, "harness_db") as db, \
             patch.object(store, "create_user", AsyncMock(return_value="u1")), \
             patch.object(store, "list_system_topics", AsyncMock(return_value=[])), \
             patch.object(store, "link_user_topic", AsyncMock()) as link:
            db.get_pool = AsyncMock(return_value=pool)
            await store.create_user_with_topics(
                "u1", "Aditya", "Pune", "09:00", ["Not A Real Beat"], [], "UTC",
            )

        link.assert_not_awaited()
        assert pool.execute.await_args.args[2] == []

    async def test_custom_topics_survive_the_prune(self):
        with patch.object(store, "harness_db") as db, \
             patch.object(store, "create_user", AsyncMock(return_value="u1")), \
             patch.object(store, "list_system_topics", AsyncMock(return_value=[])), \
             patch.object(store, "get_or_create_custom_topic", AsyncMock(return_value="chess-id")), \
             patch.object(store, "link_user_topic", AsyncMock()) as link:
            db.get_pool = AsyncMock(return_value=_fake_pool())
            await store.create_user_with_topics(
                "u1", "Aditya", "Pune", "09:00", [], ["chess"], "UTC",
            )

        link.assert_awaited_once_with("u1", "chess-id", "custom")


class TestSetDailyBriefProgress:
    async def test_updates_and_returns_true_on_match(self):
        pool = _fake_pool(execute_result="UPDATE 1")
        with patch.object(store, "harness_db") as db:
            db.get_pool = AsyncMock(return_value=pool)
            ok = await store.set_daily_brief_progress("b-1", "u1", 0.42, False)
        assert ok is True
        args = pool.execute.await_args.args
        assert args[1:] == ("b-1", "u1", 0.42, False)

    async def test_wrong_owner_returns_false(self):
        """user_id is part of the WHERE clause, not just a filter after the
        fact — a caller can only move progress on their own brief."""
        pool = _fake_pool(execute_result="UPDATE 0")
        with patch.object(store, "harness_db") as db:
            db.get_pool = AsyncMock(return_value=pool)
            ok = await store.set_daily_brief_progress("b-1", "someone-else", 0.9, True)
        assert ok is False
