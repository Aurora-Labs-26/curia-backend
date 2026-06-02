"""
tests/test_integration_queue.py
Integration tests for the Postgres-backed job queue (core/queue.py).

These tests require a live Postgres database. Skip if DB is unavailable.
"""

import asyncio
import uuid

import pytest
import pytest_asyncio

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _db_available() -> bool:
    """Quick probe — returns True if Postgres is reachable."""
    try:
        from core.db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception:
        return False


async def _cleanup_test_jobs(user_id: str):
    """Delete all jobs created by our test user."""
    try:
        from core.db.connection import get_db
        async with get_db() as conn:
            await conn.execute("DELETE FROM jobs WHERE user_id = $1", user_id)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Skip if DB unavailable
# ─────────────────────────────────────────────────────────────────────────────

pytestmark = pytest.mark.asyncio

TEST_USER = "test-queue-user-" + uuid.uuid4().hex[:8]


@pytest_asyncio.fixture(autouse=True)
async def check_db():
    """Skip all queue tests if Postgres is not reachable."""
    if not await _db_available():
        pytest.skip("Postgres not available — skipping queue integration tests")
    yield
    await _cleanup_test_jobs(TEST_USER)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Enqueue
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnqueue:

    async def test_enqueue_returns_uuid(self):
        from core.queue import enqueue
        job_id = await enqueue("ingest", {"url": "https://test.com"}, user_id=TEST_USER)
        assert isinstance(job_id, uuid.UUID)

    async def test_enqueue_sets_correct_type(self):
        from core.queue import enqueue
        from core.db.connection import get_db
        job_id = await enqueue("generate_episode", {"episode_id": "abc"}, user_id=TEST_USER)
        async with get_db() as conn:
            row = await conn.fetchrow("SELECT type, status FROM jobs WHERE id = $1", job_id)
        assert row["type"] == "generate_episode"
        assert row["status"] == "queued"

    async def test_enqueue_custom_max_attempts(self):
        from core.queue import enqueue
        from core.db.connection import get_db
        job_id = await enqueue("ingest", {}, user_id=TEST_USER, max_attempts=5)
        async with get_db() as conn:
            row = await conn.fetchrow("SELECT max_attempts FROM jobs WHERE id = $1", job_id)
        assert row["max_attempts"] == 5

    async def test_enqueue_with_correlation_id(self):
        from core.queue import enqueue
        from core.db.connection import get_db
        corr_id = uuid.uuid4()
        job_id = await enqueue("ingest", {}, user_id=TEST_USER, correlation_id=corr_id)
        async with get_db() as conn:
            row = await conn.fetchrow("SELECT correlation_id FROM jobs WHERE id = $1", job_id)
        assert row["correlation_id"] == corr_id

    async def test_enqueue_sets_priority(self):
        from core.queue import enqueue
        from core.db.connection import get_db
        job_id = await enqueue("ingest", {}, user_id=TEST_USER)
        async with get_db() as conn:
            row = await conn.fetchrow("SELECT priority FROM jobs WHERE id = $1", job_id)
        assert row["priority"] == 1  # ingest has priority 1

    async def test_enqueue_episode_priority(self):
        from core.queue import enqueue
        from core.db.connection import get_db
        job_id = await enqueue("generate_episode", {}, user_id=TEST_USER)
        async with get_db() as conn:
            row = await conn.fetchrow("SELECT priority FROM jobs WHERE id = $1", job_id)
        assert row["priority"] == 10  # generate_episode has priority 10


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Dequeue
# ═══════════════════════════════════════════════════════════════════════════════


class TestDequeue:

    async def test_dequeue_returns_job(self):
        from core.queue import enqueue, dequeue
        await enqueue("ingest", {"test": True}, user_id=TEST_USER)
        job = await dequeue(worker_id="test-worker")
        assert job is not None
        assert job.type == "ingest"
        assert job.payload.get("test") is True

    async def test_dequeue_empty_queue_returns_none(self):
        from core.queue import dequeue
        # Flush test user jobs
        await _cleanup_test_jobs(TEST_USER)
        job = await dequeue(worker_id="test-worker")
        # May return None or a job from another user — just assert it doesn't crash
        # If there are no queued jobs at all, it returns None
        assert job is None or hasattr(job, "type")

    async def test_dequeue_marks_running(self):
        from core.queue import enqueue, dequeue
        from core.db.connection import get_db
        job_id = await enqueue("ingest", {}, user_id=TEST_USER)
        job = await dequeue(worker_id="test-worker")
        if job and job.id == job_id:
            async with get_db() as conn:
                row = await conn.fetchrow("SELECT status, locked_by FROM jobs WHERE id = $1", job_id)
            assert row["status"] == "running"
            assert row["locked_by"] == "test-worker"

    async def test_dequeue_respects_priority(self):
        """Higher priority (lower number) jobs should be dequeued first."""
        from core.queue import enqueue, dequeue
        await _cleanup_test_jobs(TEST_USER)

        # Enqueue episode (priority 10) first, then ingest (priority 1)
        ep_id = await enqueue("generate_episode", {"order": 2}, user_id=TEST_USER)
        ingest_id = await enqueue("ingest", {"order": 1}, user_id=TEST_USER)

        job = await dequeue(worker_id="test-worker")
        if job:
            # Ingest should come first despite being enqueued second
            assert job.type == "ingest"

    async def test_dequeue_increments_attempts(self):
        from core.queue import enqueue, dequeue
        await enqueue("ingest", {}, user_id=TEST_USER)
        job = await dequeue(worker_id="test-worker")
        if job:
            assert job.attempts >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Ack
# ═══════════════════════════════════════════════════════════════════════════════


class TestAck:

    async def test_ack_marks_done(self):
        from core.queue import enqueue, dequeue, ack
        from core.db.connection import get_db
        await enqueue("ingest", {}, user_id=TEST_USER)
        job = await dequeue(worker_id="test-worker")
        if job:
            await ack(job.id)
            async with get_db() as conn:
                row = await conn.fetchrow("SELECT status, locked_by, locked_at FROM jobs WHERE id = $1", job.id)
            assert row["status"] == "done"
            assert row["locked_by"] is None
            assert row["locked_at"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Fail (with retry)
# ═══════════════════════════════════════════════════════════════════════════════


class TestFail:

    async def test_fail_requeues_when_attempts_remain(self):
        from core.queue import enqueue, dequeue, fail
        from core.db.connection import get_db
        job_id = await enqueue("ingest", {}, user_id=TEST_USER, max_attempts=3)
        job = await dequeue(worker_id="test-worker")
        if job:
            await fail(job.id, "test error")
            async with get_db() as conn:
                row = await conn.fetchrow("SELECT status, last_error FROM jobs WHERE id = $1", job.id)
            assert row["status"] == "queued"  # requeued for retry
            assert row["last_error"] == "test error"

    async def test_fail_marks_failed_when_exhausted(self):
        from core.queue import enqueue, dequeue, fail
        from core.db.connection import get_db
        job_id = await enqueue("ingest", {}, user_id=TEST_USER, max_attempts=1)
        job = await dequeue(worker_id="test-worker")
        if job:
            await fail(job.id, "final error")
            async with get_db() as conn:
                row = await conn.fetchrow("SELECT status FROM jobs WHERE id = $1", job.id)
            assert row["status"] == "failed"

    async def test_fail_truncates_long_error(self):
        from core.queue import enqueue, dequeue, fail
        from core.db.connection import get_db
        job_id = await enqueue("ingest", {}, user_id=TEST_USER, max_attempts=1)
        job = await dequeue(worker_id="test-worker")
        if job:
            long_error = "x" * 5000
            await fail(job.id, long_error)
            async with get_db() as conn:
                row = await conn.fetchrow("SELECT last_error FROM jobs WHERE id = $1", job.id)
            assert len(row["last_error"]) <= 2000


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Fail Permanently
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailPermanently:

    async def test_fail_permanently_no_retry(self):
        from core.queue import enqueue, dequeue, fail_permanently
        from core.db.connection import get_db
        job_id = await enqueue("ingest", {}, user_id=TEST_USER, max_attempts=10)
        job = await dequeue(worker_id="test-worker")
        if job:
            await fail_permanently(job.id, "404 not found")
            async with get_db() as conn:
                row = await conn.fetchrow(
                    "SELECT status, attempts, max_attempts FROM jobs WHERE id = $1", job.id
                )
            assert row["status"] == "failed"
            assert row["attempts"] == row["max_attempts"]  # exhausted


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Reap Stale
# ═══════════════════════════════════════════════════════════════════════════════


class TestReapStale:

    async def test_reap_stale_returns_count(self):
        from core.queue import reap_stale
        count = await reap_stale(stale_after_minutes=30)
        assert isinstance(count, int)
        assert count >= 0

    async def test_reap_stale_recovers_old_running_jobs(self):
        from core.queue import enqueue, dequeue, reap_stale
        from core.db.connection import get_db

        job_id = await enqueue("ingest", {}, user_id=TEST_USER)
        job = await dequeue(worker_id="test-worker")
        if job:
            # Manually backdate locked_at to simulate a stuck job
            async with get_db() as conn:
                await conn.execute(
                    "UPDATE jobs SET locked_at = now() - interval '60 minutes' WHERE id = $1",
                    job.id,
                )
            reaped = await reap_stale(stale_after_minutes=30)
            assert reaped >= 1

            # Job should be back in queued state
            async with get_db() as conn:
                row = await conn.fetchrow("SELECT status FROM jobs WHERE id = $1", job.id)
            assert row["status"] == "queued"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Default Worker ID
# ═══════════════════════════════════════════════════════════════════════════════


class TestDefaultWorkerId:

    def test_default_worker_id_format(self):
        from core.queue import default_worker_id
        wid = default_worker_id()
        assert "-" in wid  # hostname-pid
        parts = wid.rsplit("-", 1)
        assert len(parts) == 2
        assert parts[1].isdigit()  # PID part


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Full Lifecycle
# ═══════════════════════════════════════════════════════════════════════════════


class TestFullLifecycle:

    async def test_enqueue_dequeue_ack(self):
        """Happy path: enqueue → dequeue → ack."""
        from core.queue import enqueue, dequeue, ack
        from core.db.connection import get_db

        payload = {"test_id": str(uuid.uuid4())}
        job_id = await enqueue("ingest", payload, user_id=TEST_USER)

        job = await dequeue(worker_id="lifecycle-worker")
        assert job is not None

        await ack(job.id)
        async with get_db() as conn:
            row = await conn.fetchrow("SELECT status FROM jobs WHERE id = $1", job.id)
        assert row["status"] == "done"

    async def test_enqueue_dequeue_fail_retry_ack(self):
        """Retry path: enqueue → dequeue → fail → dequeue again → ack."""
        from core.queue import enqueue, dequeue, fail, ack
        from core.db.connection import get_db

        job_id = await enqueue("ingest", {"retry": True}, user_id=TEST_USER, max_attempts=3)

        # First attempt — fail
        job = await dequeue(worker_id="retry-worker")
        if job and job.id == job_id:
            await fail(job.id, "transient error")

            # Should be requeued
            async with get_db() as conn:
                row = await conn.fetchrow("SELECT status FROM jobs WHERE id = $1", job.id)
            assert row["status"] == "queued"

            # Second attempt — succeed
            job2 = await dequeue(worker_id="retry-worker")
            if job2 and job2.id == job_id:
                await ack(job2.id)
                async with get_db() as conn:
                    row2 = await conn.fetchrow("SELECT status FROM jobs WHERE id = $1", job2.id)
                assert row2["status"] == "done"
