"""
tests/test_worker_reliability.py
Tests for worker reliability improvements:
  1. Sync calls wrapped in run_in_executor (event loop stays responsive)
  2. reap_stale returns correct count and uses conn.fetch
  3. Handler timeout via asyncio.wait_for
  4. _maybe_reap_stale called from worker loop
"""

import asyncio
import inspect
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. generator.py — sync calls are offloaded to thread pool
# ---------------------------------------------------------------------------


class TestGeneratorAsyncOffload:
    """Verify that process_episode offloads sync functions via run_in_executor."""

    def test_process_episode_is_async(self):
        from studio.generator import process_episode
        assert inspect.iscoroutinefunction(process_episode)

    def test_generate_outline_is_sync(self):
        from studio.generator import generate_outline
        assert not inspect.iscoroutinefunction(generate_outline)

    def test_generate_transcript_is_sync(self):
        from studio.generator import generate_transcript
        assert not inspect.iscoroutinefunction(generate_transcript)

    def test_synthesize_and_stitch_is_sync(self):
        from studio.generator import synthesize_and_stitch
        assert not inspect.iscoroutinefunction(synthesize_and_stitch)

    def test_process_episode_uses_run_in_executor(self):
        """The source code of process_episode must call run_in_executor for
        generate_outline, generate_transcript, and synthesize_and_stitch."""
        from studio.generator import process_episode
        source = inspect.getsource(process_episode)
        assert "run_in_executor" in source, (
            "process_episode must use run_in_executor to offload sync calls"
        )
        assert source.count("run_in_executor") >= 3, (
            "Expected at least 3 run_in_executor calls "
            "(outline, transcript, synthesize_and_stitch)"
        )


# ---------------------------------------------------------------------------
# 2. core/queue.py — reap_stale fix
# ---------------------------------------------------------------------------


class TestReapStale:
    def test_reap_stale_is_async(self):
        from core.queue import reap_stale
        assert inspect.iscoroutinefunction(reap_stale)

    def test_reap_stale_uses_fetch_not_fetchrow(self):
        """reap_stale must use conn.fetch (returns list of rows) not
        conn.fetchrow (returns single row with broken RETURNING)."""
        from core.queue import reap_stale
        source = inspect.getsource(reap_stale)
        assert ".fetch(" in source, "reap_stale must use conn.fetch"
        assert "RETURNING id" in source, "reap_stale must RETURNING id"
        assert "WHERE FALSE" not in source, (
            "The broken 'SELECT COUNT(*) ... WHERE FALSE' must be removed"
        )


# ---------------------------------------------------------------------------
# 3. worker/main.py — handler timeout
# ---------------------------------------------------------------------------


class TestHandlerTimeout:
    def test_timeout_constant_exists(self):
        from worker.main import HANDLER_TIMEOUT_SECONDS
        assert HANDLER_TIMEOUT_SECONDS == 600

    def test_timeout_is_env_configurable(self):
        with patch.dict("os.environ", {"CURIA_HANDLER_TIMEOUT_SECONDS": "300"}):
            import importlib
            import worker.main as wm
            importlib.reload(wm)
            assert wm.HANDLER_TIMEOUT_SECONDS == 300
            # Restore
            with patch.dict("os.environ", {}, clear=False):
                importlib.reload(wm)

    def test_process_one_uses_wait_for(self):
        from worker.main import _process_one
        source = inspect.getsource(_process_one)
        assert "asyncio.wait_for" in source, (
            "_process_one must wrap handler call in asyncio.wait_for"
        )
        assert "HANDLER_TIMEOUT_SECONDS" in source

    async def test_timeout_fires_on_slow_handler(self):
        """Simulate a handler that takes longer than the timeout."""
        from core.queue import Job
        from uuid import uuid4

        slow_job = Job(
            id=uuid4(),
            type="test_slow",
            payload={},
            user_id="test",
            attempts=1,
            max_attempts=3,
            correlation_id=None,
        )

        async def slow_handler(payload):
            await asyncio.sleep(10)

        mock_fail = AsyncMock()
        mock_ack = AsyncMock()
        mock_dequeue = AsyncMock(return_value=slow_job)

        with patch("worker.main.dequeue", mock_dequeue), \
             patch("worker.main.fail", mock_fail), \
             patch("worker.main.ack", mock_ack), \
             patch("worker.main.HANDLERS", {"test_slow": slow_handler}), \
             patch("worker.main.HANDLER_TIMEOUT_SECONDS", 0.1):
            from worker.main import _process_one
            result = await _process_one("test-worker")

        assert result is True
        mock_fail.assert_called_once()
        fail_args = mock_fail.call_args
        assert "timed out" in str(fail_args)
        mock_ack.assert_not_called()


# ---------------------------------------------------------------------------
# 4. worker/main.py — reap_stale integration
# ---------------------------------------------------------------------------


class TestReapStaleIntegration:
    def test_reap_stale_imported_in_worker(self):
        from worker.main import reap_stale  # noqa: F401

    def test_maybe_reap_stale_exists(self):
        from worker.main import _maybe_reap_stale
        assert inspect.iscoroutinefunction(_maybe_reap_stale)

    def test_reap_interval_constant(self):
        from worker.main import REAP_INTERVAL_SECONDS
        assert REAP_INTERVAL_SECONDS == 300

    def test_main_loop_calls_reap(self):
        """The main() loop source must reference _maybe_reap_stale."""
        from worker.main import main
        source = inspect.getsource(main)
        assert "_maybe_reap_stale" in source

    async def test_maybe_reap_stale_throttled(self):
        """_maybe_reap_stale should skip if called within REAP_INTERVAL_SECONDS."""
        import worker.main as wm

        mock_reap = AsyncMock(return_value=0)
        with patch("worker.main.reap_stale", mock_reap):
            wm._last_reap_at = 0
            await wm._maybe_reap_stale()
            assert mock_reap.call_count == 1

            # Second call within interval — should be throttled
            await wm._maybe_reap_stale()
            assert mock_reap.call_count == 1

    async def test_maybe_reap_stale_runs_after_interval(self):
        """_maybe_reap_stale should run again after REAP_INTERVAL_SECONDS."""
        import worker.main as wm

        mock_reap = AsyncMock(return_value=2)
        with patch("worker.main.reap_stale", mock_reap), \
             patch("worker.main.REAP_INTERVAL_SECONDS", 0):
            wm._last_reap_at = 0
            await wm._maybe_reap_stale()
            await wm._maybe_reap_stale()
            assert mock_reap.call_count == 2


# ---------------------------------------------------------------------------
# 5. Timeout doesn't break normal fast handlers
# ---------------------------------------------------------------------------


class TestTimeoutNormalOperation:
    async def test_fast_handler_succeeds(self):
        """A handler that completes quickly should ack normally."""
        from core.queue import Job
        from uuid import uuid4

        fast_job = Job(
            id=uuid4(),
            type="test_fast",
            payload={},
            user_id="test",
            attempts=1,
            max_attempts=3,
            correlation_id=None,
        )

        async def fast_handler(payload):
            pass

        mock_ack = AsyncMock()
        mock_fail = AsyncMock()
        mock_dequeue = AsyncMock(return_value=fast_job)

        with patch("worker.main.dequeue", mock_dequeue), \
             patch("worker.main.ack", mock_ack), \
             patch("worker.main.fail", mock_fail), \
             patch("worker.main.HANDLERS", {"test_fast": fast_handler}), \
             patch("worker.main.HANDLER_TIMEOUT_SECONDS", 5):
            from worker.main import _process_one
            result = await _process_one("test-worker")

        assert result is True
        mock_ack.assert_called_once()
        mock_fail.assert_not_called()

    async def test_empty_queue_returns_false(self):
        """When dequeue returns None, _process_one should return False."""
        mock_dequeue = AsyncMock(return_value=None)

        with patch("worker.main.dequeue", mock_dequeue):
            from worker.main import _process_one
            result = await _process_one("test-worker")

        assert result is False
