"""
tests/test_worker_handlers.py
Tests for worker handler logic — validation, routing, retry semantics.

Verifies:
  - generate_from_source._validate_format returns canonical names
  - generate_from_source._validate_format falls back to clarity_engine
  - handle_ingest raises on missing source_id
  - handle_ingest computes is_final_attempt correctly
  - handle_generate_episode raises on missing episode_id
  - _notify_episode_ready swallows exceptions gracefully
  - Handler registry contains all expected job types
"""

from unittest.mock import AsyncMock, patch, MagicMock

import pytest


class TestValidateFormat:

    def test_known_format_passes_through(self):
        from worker.handlers.generate_from_source import _validate_format
        assert _validate_format("narrative_drift") == "narrative_drift"
        assert _validate_format("clarity_engine") == "clarity_engine"
        assert _validate_format("momentum_loop") == "momentum_loop"
        assert _validate_format("exploration_engine") == "exploration_engine"

    def test_unknown_format_falls_back(self):
        from worker.handlers.generate_from_source import _validate_format
        assert _validate_format("nonexistent") == "clarity_engine"

    def test_none_falls_back(self):
        from worker.handlers.generate_from_source import _validate_format
        assert _validate_format(None) == "clarity_engine"

    def test_empty_string_falls_back(self):
        from worker.handlers.generate_from_source import _validate_format
        assert _validate_format("") == "clarity_engine"


class TestHandleIngest:

    @pytest.mark.asyncio
    async def test_raises_on_missing_source_id(self):
        from worker.handlers.ingest import handle_ingest
        with pytest.raises(ValueError, match="missing source_id"):
            await handle_ingest({})

    @pytest.mark.asyncio
    async def test_raises_on_empty_payload(self):
        from worker.handlers.ingest import handle_ingest
        with pytest.raises(ValueError, match="missing source_id"):
            await handle_ingest({"user_id": "u1"})

    @pytest.mark.asyncio
    async def test_final_attempt_detection(self):
        """On final attempt, is_final_attempt=True is passed to process_source."""
        from worker.handlers import ingest as ingest_mod

        with patch.object(ingest_mod, "process_source", new_callable=AsyncMock) as mock_ps, \
             patch.object(ingest_mod, "db_fetchrow", new_callable=AsyncMock, return_value=None):
            await ingest_mod.handle_ingest({
                "source_id": "src-1",
                "__attempt__": 3,
                "__max_attempts__": 3,
            })
            mock_ps.assert_called_once_with(source_id="src-1", is_final_attempt=True)

    @pytest.mark.asyncio
    async def test_non_final_attempt(self):
        from worker.handlers import ingest as ingest_mod

        with patch.object(ingest_mod, "process_source", new_callable=AsyncMock) as mock_ps, \
             patch.object(ingest_mod, "db_fetchrow", new_callable=AsyncMock, return_value=None):
            await ingest_mod.handle_ingest({
                "source_id": "src-1",
                "__attempt__": 1,
                "__max_attempts__": 3,
            })
            mock_ps.assert_called_once_with(source_id="src-1", is_final_attempt=False)

    @pytest.mark.asyncio
    async def test_skips_auto_generate_when_false(self):
        from worker.handlers import ingest as ingest_mod

        with patch.object(ingest_mod, "process_source", new_callable=AsyncMock), \
             patch.object(ingest_mod, "db_fetchrow", new_callable=AsyncMock, return_value={"user_id": "u1"}), \
             patch.object(ingest_mod, "enqueue", new_callable=AsyncMock) as mock_enqueue:
            await ingest_mod.handle_ingest({
                "source_id": "src-1",
                "auto_generate": False,
            })
            mock_enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_generate_ideas_if_already_queued(self):
        from worker.handlers import ingest as ingest_mod

        with patch.object(ingest_mod, "process_source", new_callable=AsyncMock), \
             patch.object(ingest_mod, "db_fetchrow", new_callable=AsyncMock, side_effect=[
                 {"user_id": "u1"},
                 {"id": "existing-job"},
             ]), \
             patch.object(ingest_mod, "enqueue", new_callable=AsyncMock) as mock_enqueue:
            await ingest_mod.handle_ingest({"source_id": "src-1"})
            mock_enqueue.assert_not_called()


class TestHandleGenerateEpisode:

    @pytest.mark.asyncio
    async def test_raises_on_missing_episode_id(self):
        from worker.handlers.generate_episode import handle_generate_episode
        with pytest.raises(ValueError, match="missing episode_id"):
            await handle_generate_episode({})

    @pytest.mark.asyncio
    async def test_calls_process_episode(self):
        from worker.handlers import generate_episode as ep_mod

        with patch.object(ep_mod, "process_episode", new_callable=AsyncMock) as mock_pe, \
             patch.object(ep_mod, "_notify_episode_ready", new_callable=AsyncMock):
            await ep_mod.handle_generate_episode({"episode_id": "ep-123"})
            mock_pe.assert_called_once_with(episode_id="ep-123")

    @pytest.mark.asyncio
    async def test_notify_swallows_exceptions(self):
        from worker.handlers.generate_episode import _notify_episode_ready

        with patch("worker.handlers.generate_episode.db_fetchrow", new_callable=AsyncMock, side_effect=Exception("db down")):
            await _notify_episode_ready("ep-123")

    @pytest.mark.asyncio
    async def test_notify_skips_when_no_fcm_token(self):
        from worker.handlers.generate_episode import _notify_episode_ready

        with patch("worker.handlers.generate_episode.db_fetchrow", new_callable=AsyncMock, return_value={"title": "Test", "fcm_token": None}):
            await _notify_episode_ready("ep-123")


class TestHandlerRegistry:

    def test_all_handlers_registered(self):
        from worker.handlers import HANDLERS
        expected = {"ingest", "generate_ideas", "generate_episode", "generate_from_source", "optimize"}
        assert set(HANDLERS.keys()) == expected

    def test_all_handlers_are_async(self):
        import asyncio
        from worker.handlers import HANDLERS
        for name, handler in HANDLERS.items():
            assert asyncio.iscoroutinefunction(handler), f"Handler '{name}' is not async"
