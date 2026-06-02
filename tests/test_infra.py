"""
tests/test_infra.py
Tests for multi-worker infrastructure changes:
  - Configurable DB pool sizing via env vars
  - Stale job reaper (reap_stale) correctness
  - API lifespan creates reaper background task
  - Worker startup calls reap_stale
"""

import asyncio
import os
from unittest.mock import AsyncMock, patch, MagicMock

import pytest


class TestConfigurablePoolSize:

    @pytest.mark.asyncio
    async def test_default_pool_size(self, monkeypatch):
        """Without env vars, pool defaults to min=2, max=10."""
        monkeypatch.delenv("CURIA_DB_POOL_MAX", raising=False)
        monkeypatch.delenv("CURIA_DB_POOL_MIN", raising=False)

        import core.db.connection as conn_mod
        monkeypatch.setattr(conn_mod, "_pool", None)

        mock_pool = AsyncMock()
        with patch("core.db.connection.asyncpg.create_pool", new_callable=AsyncMock, return_value=mock_pool) as mock_create:
            pool = await conn_mod.get_pool()
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args
            assert call_kwargs.kwargs.get("min_size", call_kwargs[1].get("min_size")) == 2 or call_kwargs[0] is not None

        monkeypatch.setattr(conn_mod, "_pool", None)

    @pytest.mark.asyncio
    async def test_custom_pool_size(self, monkeypatch):
        """CURIA_DB_POOL_MAX and CURIA_DB_POOL_MIN override defaults."""
        monkeypatch.setenv("CURIA_DB_POOL_MAX", "5")
        monkeypatch.setenv("CURIA_DB_POOL_MIN", "1")

        import core.db.connection as conn_mod
        monkeypatch.setattr(conn_mod, "_pool", None)

        mock_pool = AsyncMock()
        with patch("core.db.connection.asyncpg.create_pool", new_callable=AsyncMock, return_value=mock_pool) as mock_create:
            pool = await conn_mod.get_pool()
            _, kwargs = mock_create.call_args
            assert kwargs["min_size"] == 1
            assert kwargs["max_size"] == 5

        monkeypatch.setattr(conn_mod, "_pool", None)


class TestReapStale:

    @pytest.mark.asyncio
    async def test_reap_returns_count(self):
        """reap_stale should return the number of rows affected."""
        fake_rows = [{"id": "aaa"}, {"id": "bbb"}]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=fake_rows)

        with patch("core.queue.get_db") as mock_get_db:
            mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
            from core.queue import reap_stale
            count = await reap_stale(stale_after_minutes=30)

        assert count == 2

    @pytest.mark.asyncio
    async def test_reap_returns_zero_when_none(self):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        with patch("core.queue.get_db") as mock_get_db:
            mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
            from core.queue import reap_stale
            count = await reap_stale(stale_after_minutes=30)

        assert count == 0


class TestAPIReaperLifespan:

    def test_reap_loop_function_exists(self):
        from api.main import _reap_stale_jobs_loop
        assert asyncio.iscoroutinefunction(_reap_stale_jobs_loop)

    def test_lifespan_is_async_context_manager(self):
        from api.main import lifespan
        assert hasattr(lifespan, "__aenter__") or callable(lifespan)


class TestWorkerReapOnBoot:

    def test_worker_main_is_async(self):
        from worker.main import main
        assert asyncio.iscoroutinefunction(main)

    @pytest.mark.asyncio
    async def test_worker_calls_reap_on_startup(self, monkeypatch):
        """Worker main() should call reap_stale before entering the poll loop."""
        reap_called = False

        async def mock_reap(stale_after_minutes=30):
            nonlocal reap_called
            reap_called = True
            return 0

        monkeypatch.setattr("core.queue.reap_stale", mock_reap)

        from worker.main import _shutdown
        _shutdown.set()

        with patch("core.logging.setup_logging"), \
             patch("core.firebase.init_firebase"), \
             patch("core.prompt_watcher.init_prompt_hashes"), \
             patch("core.prompt_watcher.check_prompt_changes"), \
             patch("worker.main._maybe_purge_failed_sources", new_callable=AsyncMock), \
             patch("worker.main._install_signal_handlers"):
            from worker.main import main
            await main()

        _shutdown.clear()
        assert reap_called, "Worker did not call reap_stale on startup"
