"""
tests/test_analytics.py
Unit tests for core/analytics.py (v2.9 PostHog backend analytics).

All mock-only: the PostHog client is patched, so `posthog` need not be installed.
The disabled path (no token) returns before any import, matching prod's safe default.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.analytics as analytics


@pytest.fixture(autouse=True)
def _reset_client_singleton():
    """The client is cached in module globals — reset around every test."""
    analytics._client = None
    analytics._client_initialized = False
    yield
    analytics._client = None
    analytics._client_initialized = False


# ---------------------------------------------------------------------------
# _get_client — disabled when token unset
# ---------------------------------------------------------------------------


class TestGetClient:
    def test_noop_when_token_unset(self, monkeypatch):
        monkeypatch.delenv("POSTHOG_PROJECT_TOKEN", raising=False)
        assert analytics._get_client() is None

    def test_result_is_cached(self, monkeypatch):
        monkeypatch.delenv("POSTHOG_PROJECT_TOKEN", raising=False)
        assert analytics._get_client() is None
        # second call short-circuits on the initialized flag
        assert analytics._get_client() is None
        assert analytics._client_initialized is True


# ---------------------------------------------------------------------------
# resolve_distinct_id — firebase_uid preferred, user_id fallback
# ---------------------------------------------------------------------------


class TestResolveDistinctId:
    async def test_returns_firebase_uid_when_present(self):
        with patch("core.analytics.db_fetchrow",
                   AsyncMock(return_value={"firebase_uid": "fb-123"})):
            assert await analytics.resolve_distinct_id("user-1") == "fb-123"

    async def test_falls_back_to_user_id_when_firebase_uid_null(self):
        with patch("core.analytics.db_fetchrow",
                   AsyncMock(return_value={"firebase_uid": None})):
            assert await analytics.resolve_distinct_id("user-1") == "user-1"

    async def test_falls_back_to_user_id_when_no_row(self):
        with patch("core.analytics.db_fetchrow", AsyncMock(return_value=None)):
            assert await analytics.resolve_distinct_id("user-1") == "user-1"


# ---------------------------------------------------------------------------
# capture — fire-and-forget, never raises
# ---------------------------------------------------------------------------


class TestCapture:
    def test_noop_when_no_client(self, monkeypatch):
        monkeypatch.delenv("POSTHOG_PROJECT_TOKEN", raising=False)
        # Must simply return; no client, no exception.
        analytics.capture("distinct-1", "some_event", {"a": 1})

    def test_forwards_args_to_client(self, monkeypatch):
        monkeypatch.setenv("CURIA_ENV", "prod")
        client = MagicMock()
        with patch("core.analytics._get_client", return_value=client):
            analytics.capture("distinct-1", "episode_generated", {"episode_id": "e1"})
        client.capture.assert_called_once_with(
            distinct_id="distinct-1", event="episode_generated",
            properties={"episode_id": "e1", "environment": "production"},
        )

    def test_defaults_properties_to_empty_dict(self, monkeypatch):
        monkeypatch.setenv("CURIA_ENV", "dev")
        client = MagicMock()
        with patch("core.analytics._get_client", return_value=client):
            analytics.capture("d", "evt")
        client.capture.assert_called_once_with(
            distinct_id="d", event="evt", properties={"environment": "development"},
        )

    def test_environment_normalizes_dev_and_prod_aliases(self, monkeypatch):
        client = MagicMock()
        with patch("core.analytics._get_client", return_value=client):
            monkeypatch.setenv("CURIA_ENV", "dev")
            analytics.capture("d", "evt")
            assert client.capture.call_args.kwargs["properties"]["environment"] == "development"
            monkeypatch.setenv("CURIA_ENV", "prod")
            analytics.capture("d", "evt")
            assert client.capture.call_args.kwargs["properties"]["environment"] == "production"
            # Unrecognized values pass through unchanged (e.g. "preview").
            monkeypatch.setenv("CURIA_ENV", "preview")
            analytics.capture("d", "evt")
            assert client.capture.call_args.kwargs["properties"]["environment"] == "preview"

    def test_never_raises_when_client_errors(self):
        bad = MagicMock()
        bad.capture.side_effect = RuntimeError("posthog down")
        with patch("core.analytics._get_client", return_value=bad):
            analytics.capture("d", "evt")  # must not raise
        bad.capture.assert_called_once()


# ---------------------------------------------------------------------------
# track — resolve then capture, never raises
# ---------------------------------------------------------------------------


class TestTrack:
    async def test_resolves_then_captures(self, monkeypatch):
        monkeypatch.setenv("CURIA_ENV", "dev")
        client = MagicMock()
        with patch("core.analytics._get_client", return_value=client), \
             patch("core.analytics.db_fetchrow",
                   AsyncMock(return_value={"firebase_uid": "fb-9"})):
            await analytics.track("user-1", "source_ingested", {"source_id": "s1"})
        client.capture.assert_called_once_with(
            distinct_id="fb-9", event="source_ingested",
            properties={"source_id": "s1", "environment": "development"},
        )

    async def test_never_raises_when_resolve_fails(self):
        with patch("core.analytics.db_fetchrow",
                   AsyncMock(side_effect=RuntimeError("db down"))):
            await analytics.track("user-1", "evt")  # must not raise
