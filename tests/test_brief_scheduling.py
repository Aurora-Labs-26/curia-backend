"""
tests/test_brief_scheduling.py — the two brief cron jobs (worker/main.py),
the due-users store query, and the brief_ready push (fired from
worker/handlers/brief.py once generation actually succeeds).
"""

from unittest.mock import AsyncMock, MagicMock, patch

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
        # Refined at merge (v3.1 review v1.md §4.2): failed rows DO retry —
        # the original intent of asserting 'failed' was absent here — but only
        # within a bounded window, so 'failed' may now appear in the SQL
        # strictly as part of a created_at-bounded exclusion (see
        # TestFailedRetryCap), never as a blanket exclusion.
        assert "db.status = 'failed'" in sql and "created_at" in sql

    async def test_empty_when_nobody_due(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        with patch.object(store, "harness_db") as db:
            db.get_pool = AsyncMock(return_value=pool)
            assert await store.list_due_users_for_generation() == []


class TestIsUserDueNow:
    """GET /brief/today's single-user version of the same due check, used to
    trigger generation eagerly instead of waiting up to 15 minutes for the
    next dispatch poll."""

    async def test_true_when_scheduled_time_passed(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"due": True})
        with patch.object(store, "harness_db") as db:
            db.get_pool = AsyncMock(return_value=pool)
            assert await store.is_user_due_now("u1") is True
        sql = pool.fetchrow.await_args.args[0]
        assert "u.timezone" in sql
        assert "u.scheduled_time" in sql

    async def test_false_when_not_due_yet(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"due": False})
        with patch.object(store, "harness_db") as db:
            db.get_pool = AsyncMock(return_value=pool)
            assert await store.is_user_due_now("u1") is False

    async def test_false_when_user_not_found(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        with patch.object(store, "harness_db") as db:
            db.get_pool = AsyncMock(return_value=pool)
            assert await store.is_user_due_now("ghost") is False


class TestBriefDispatchJob:
    async def test_enqueues_background_lane_per_due_user(self):
        from worker.main import _brief_dispatch_body as _run_brief_dispatch_job

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
        from worker.main import _brief_dispatch_body as _run_brief_dispatch_job

        with patch("brief.store.list_due_users_for_generation", AsyncMock(return_value=[])), \
             patch("core.queue.enqueue", AsyncMock()) as enq:
            await _run_brief_dispatch_job()
        enq.assert_not_awaited()

    async def test_query_failure_does_not_raise(self):
        """A bad run must not crash the scheduler thread — APScheduler would
        otherwise silently drop the job's future runs."""
        from worker.main import _brief_dispatch_body as _run_brief_dispatch_job

        with patch("brief.store.list_due_users_for_generation",
                   AsyncMock(side_effect=RuntimeError("db down"))):
            await _run_brief_dispatch_job()  # must not raise


class TestBriefPreoptJob:
    async def test_runs_preopt_and_logs_totals(self):
        from worker.main import _brief_preopt_body as _run_brief_preopt_job

        with patch("brief.preopt_runner.run_preopt",
                   AsyncMock(return_value={"topics": [], "totals": {"topics_processed": 7}})) as run:
            await _run_brief_preopt_job()
        run.assert_awaited_once()

    async def test_failure_does_not_raise(self):
        from worker.main import _brief_preopt_body as _run_brief_preopt_job

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


# ---------------------------------------------------------------------------
# Merge-review gap tests (v3.1 review v1.md §4.1–4.3)
# ---------------------------------------------------------------------------


class TestTimezoneValidation:
    """§4.1: one garbage timezone row makes list_due_users_for_generation throw
    for ALL users (single query) — so the API edge must refuse invalid IANA
    names before they reach the table."""

    def test_put_preferences_rejects_garbage_timezone(self):
        from tests.test_brief_wiring import _client
        r = _client().put("/brief/preferences", json={
            "display_name": "A", "timezone": "Bengaluru/Wrong"})
        assert r.status_code == 422

    def test_put_preferences_accepts_valid_iana(self):
        from tests.test_brief_wiring import _client
        with patch("api.routes.brief.store") as st:
            st.create_user_with_topics = AsyncMock()
            st.get_user = AsyncMock(return_value=None)
            r = _client().put("/brief/preferences", json={
                "display_name": "A", "timezone": "Asia/Kolkata"})
        assert r.status_code == 200
        # signature: (..., custom_topics, timezone, location_country)
        assert st.create_user_with_topics.await_args.args[6] == "Asia/Kolkata"


class TestSchedulerGating:
    """§4.3: both worker services run _start_scheduler — the brief jobs must be
    gateable to exactly one service or Pre-Opt (Sonnet-heavy) runs twice."""

    def _jobs_added(self, env):
        import worker.main as wm
        sched = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        with patch.object(wm, "AsyncIOScheduler", return_value=sched), \
             patch.dict("os.environ", env, clear=False):
            wm._start_scheduler()
        return [str(c.args[0].__name__ if hasattr(c.args[0], "__name__") else c.args[0])
                for c in sched.add_job.call_args_list]

    def test_brief_jobs_on_by_default(self):
        jobs = self._jobs_added({"CURIA_BRIEF_JOBS": ""})
        assert any("brief_preopt" in j for j in jobs)
        assert any("brief_dispatch" in j for j in jobs)

    def test_brief_jobs_disabled_by_flag(self):
        jobs = self._jobs_added({"CURIA_BRIEF_JOBS": "0"})
        assert not any("brief" in j for j in jobs)
        assert len(jobs) == 2      # the two notification crons stay


class TestFailedRetryCap:
    """§4.2: a permanently failing brief must not re-enqueue every 15 minutes
    all day — failed rows stop retrying 2h after the first attempt."""

    async def test_query_bounds_failed_retries_by_created_at(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        with patch.object(store, "harness_db") as db:
            db.get_pool = AsyncMock(return_value=pool)
            await store.list_due_users_for_generation()
        sql = pool.fetch.await_args.args[0]
        assert "failed" in sql, "failed rows must appear in the exclusion logic"
        assert "created_at" in sql and "interval" in sql, \
            "failed-row exclusion must be time-bounded, not permanent or unbounded"


class TestSchedulerAdvisoryLock:
    """Autoscaled background workers each run APScheduler — without a
    cross-task lock, N tasks means N concurrent Pre-Opt runs (Sonnet cost
    xN) every 6h. pg_try_advisory_lock makes exactly one task win; losers
    skip silently. Session-scoped: the SAME connection must hold the lock
    for the job's duration and release it even on failure."""

    def _conn(self, got):
        conn = AsyncMock()
        conn.fetchval = AsyncMock(side_effect=[got, True])   # try_lock, unlock
        pool = MagicMock()
        acq = MagicMock()
        acq.__aenter__ = AsyncMock(return_value=conn)
        acq.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=acq)
        return pool, conn

    async def test_winner_runs_job_and_unlocks(self):
        from worker import main as wm
        pool, conn = self._conn(got=True)
        job = AsyncMock()
        with patch("core.db.connection.get_pool", AsyncMock(return_value=pool)):
            await wm._run_exclusive(wm.LOCK_BRIEF_PREOPT, "preopt", job)
        job.assert_awaited_once()
        assert conn.fetchval.await_count == 2
        assert "pg_try_advisory_lock" in conn.fetchval.await_args_list[0].args[0]
        assert "pg_advisory_unlock" in conn.fetchval.await_args_list[1].args[0]

    async def test_loser_skips_job_without_unlocking(self):
        from worker import main as wm
        pool, conn = self._conn(got=False)
        job = AsyncMock()
        with patch("core.db.connection.get_pool", AsyncMock(return_value=pool)):
            await wm._run_exclusive(wm.LOCK_BRIEF_PREOPT, "preopt", job)
        job.assert_not_awaited()
        assert conn.fetchval.await_count == 1     # no spurious unlock

    async def test_unlock_happens_even_when_job_crashes(self):
        from worker import main as wm
        pool, conn = self._conn(got=True)
        job = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("core.db.connection.get_pool", AsyncMock(return_value=pool)):
            await wm._run_exclusive(wm.LOCK_BRIEF_PREOPT, "x", job)  # must not raise
        assert "pg_advisory_unlock" in conn.fetchval.await_args_list[1].args[0]

    async def test_jobs_use_distinct_lock_ids(self):
        from worker import main as wm
        assert wm.LOCK_BRIEF_PREOPT != wm.LOCK_BRIEF_DISPATCH

    async def test_cron_jobs_are_wrapped(self):
        from worker import main as wm
        with patch.object(wm, "_run_exclusive", AsyncMock()) as ex:
            await wm._run_brief_preopt_job()
            await wm._run_brief_dispatch_job()
        assert ex.await_count == 2
        assert {c.args[0] for c in ex.await_args_list} == {wm.LOCK_BRIEF_PREOPT, wm.LOCK_BRIEF_DISPATCH}
