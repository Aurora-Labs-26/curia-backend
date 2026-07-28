"""
tests/test_brief_scheduling.py — the two brief cron jobs (worker/main.py),
the due-users store query, and the brief_ready push (fired from
worker/handlers/brief.py once generation actually succeeds).
"""

from unittest.mock import AsyncMock, patch

import pytest

from brief import store


class TestListDueUsersForGeneration:
    """The dispatch cron's targeting query. Real DB behavior (timezone-aware
    comparison, the anti-join against daily_briefs) is exercised live against
    Postgres in test_brief_store_prefs.py's sibling coverage of other store
    functions; here we verify the call shape and the local_date passthrough
    that the dispatch job depends on."""

    async def test_returns_user_id_and_local_date_pairs(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": "u1", "local_date": __import__("datetime").date(2026, 7, 28)},
            {"id": "u2", "local_date": __import__("datetime").date(2026, 7, 27)},
        ])
        with patch.object(store, "harness_db") as db:
            db.get_pool = AsyncMock(return_value=pool)
            result = await store.list_due_users_for_generation()

        assert result == [
            {"user_id": "u1", "local_date": "2026-07-28"},
            {"user_id": "u2", "local_date": "2026-07-27"},
        ]
        # It's a timezone-aware comparison against each user's own zone, not a
        # blanket UTC check — the query itself must do the conversion.
        sql = pool.fetch.await_args.args[0]
        assert "u.timezone" in sql
        assert "u.scheduled_time" in sql
        # A failed row must NOT be in the exclusion set — otherwise a failed
        # generation would silently never retry until the next calendar day.
        # Only an in-flight or already-succeeded row should block re-enqueue.
        assert "'generating', 'ready'" in sql or "'ready', 'generating'" in sql
        assert "'failed'" not in sql

    async def test_empty_when_nobody_due(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        with patch.object(store, "harness_db") as db:
            db.get_pool = AsyncMock(return_value=pool)
            assert await store.list_due_users_for_generation() == []


class TestBriefDispatchJob:
    async def test_enqueues_background_lane_per_due_user(self):
        from worker.main import _run_brief_dispatch_job

        due = [
            {"user_id": "u1", "local_date": "2026-07-28"},
            {"user_id": "u2", "local_date": "2026-07-28"},
        ]
        with patch("brief.store.list_due_users_for_generation", AsyncMock(return_value=due)), \
             patch("core.queue.enqueue", AsyncMock(return_value="job-1")) as enq:
            await _run_brief_dispatch_job()

        assert enq.await_count == 2
        for call in enq.await_args_list:
            assert call.kwargs["lane"] == "background"
            assert call.kwargs["type"] == "generate_brief"
        assert enq.await_args_list[0].kwargs["payload"] == {"user_id": "u1", "date": "2026-07-28"}

    async def test_no_due_users_enqueues_nothing(self):
        from worker.main import _run_brief_dispatch_job

        with patch("brief.store.list_due_users_for_generation", AsyncMock(return_value=[])), \
             patch("core.queue.enqueue", AsyncMock()) as enq:
            await _run_brief_dispatch_job()
        enq.assert_not_awaited()

    async def test_query_failure_does_not_raise(self):
        """A bad run must not crash the scheduler thread — APScheduler would
        otherwise silently drop the job's future runs."""
        from worker.main import _run_brief_dispatch_job

        with patch("brief.store.list_due_users_for_generation",
                   AsyncMock(side_effect=RuntimeError("db down"))):
            await _run_brief_dispatch_job()  # must not raise


class TestBriefPreoptJob:
    async def test_runs_preopt_and_logs_totals(self):
        from worker.main import _run_brief_preopt_job

        with patch("brief.preopt_runner.run_preopt",
                   AsyncMock(return_value={"topics": [], "totals": {"topics_processed": 7}})) as run:
            await _run_brief_preopt_job()
        run.assert_awaited_once()

    async def test_failure_does_not_raise(self):
        from worker.main import _run_brief_preopt_job

        with patch("brief.preopt_runner.run_preopt", AsyncMock(side_effect=RuntimeError("llm down"))):
            await _run_brief_preopt_job()  # must not raise


class TestBriefReadyPush:
    """worker/handlers/brief.py fires the push itself, after the audio attempt
    — not core/notifications.py's two batch-scan crons, which have nothing to
    do with a single just-generated brief."""

    async def test_sends_push_when_ready(self):
        from worker.handlers import brief as h

        runner = AsyncMock(return_value={"status": "ready", "brief_id": "b-1"})
        audio = AsyncMock(return_value="key")
        push = AsyncMock()
        with patch.object(h, "generate_brief_for_user", runner), \
             patch("brief.audio.render_brief_audio", audio), \
             patch("core.notifications.send_brief_ready", push):
            await h.handle_generate_brief({"user_id": "u1", "date": "2026-07-28"})
        push.assert_awaited_once_with("u1")

    async def test_no_push_when_generation_failed(self):
        from worker.handlers import brief as h

        runner = AsyncMock(return_value={"status": "failed"})
        push = AsyncMock()
        with patch.object(h, "generate_brief_for_user", runner), \
             patch("core.notifications.send_brief_ready", push):
            await h.handle_generate_brief({"user_id": "u1"})
        push.assert_not_awaited()

    async def test_push_failure_does_not_raise(self):
        """Same posture as the audio pass right above it: a push provider
        outage must not fail the job — the brief is already ready."""
        from worker.handlers import brief as h

        runner = AsyncMock(return_value={"status": "ready", "brief_id": "b-1"})
        audio = AsyncMock(return_value="key")
        push = AsyncMock(side_effect=RuntimeError("fcm down"))
        with patch.object(h, "generate_brief_for_user", runner), \
             patch("brief.audio.render_brief_audio", audio), \
             patch("core.notifications.send_brief_ready", push):
            await h.handle_generate_brief({"user_id": "u1"})  # must not raise


class TestSendBriefReady:
    async def test_no_token_skips_silently(self):
        from core.notifications import send_brief_ready

        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"fcm_token": None})
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("core.db.connection.get_db", return_value=ctx):
            await send_brief_ready("u1")  # must not raise, no send attempted

    async def test_already_sent_today_is_not_resent(self):
        from core.notifications import send_brief_ready

        conn = AsyncMock()
        conn.fetchrow = AsyncMock(side_effect=[
            {"fcm_token": "tok"},  # user lookup
            None,                  # _claim_send's INSERT ... RETURNING -> no row = already claimed
        ])
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("core.db.connection.get_db", return_value=ctx), \
             patch("core.notifications._send_fcm", AsyncMock()) as send:
            await send_brief_ready("u1")
        send.assert_not_awaited()

    async def test_sends_and_claims_when_new(self):
        from core.notifications import send_brief_ready

        conn = AsyncMock()
        conn.fetchrow = AsyncMock(side_effect=[
            {"fcm_token": "tok"},
            {"1": 1},  # claim won
        ])
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("core.db.connection.get_db", return_value=ctx), \
             patch("core.notifications._send_fcm", AsyncMock()) as send:
            await send_brief_ready("u1")
        send.assert_awaited_once()
        assert send.await_args.args[0] == "tok"
