"""
tests/test_apple.py
Unit tests for core/apple.py — Sign in with Apple token exchange & revocation.
All external calls (httpx, jwt, env vars) are mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import core.apple as apple


# ---------------------------------------------------------------------------
# is_configured / _client_ids
# ---------------------------------------------------------------------------


class TestIsConfigured:
    @patch.dict("os.environ", {
        "APPLE_TEAM_ID": "TEAM123456",
        "APPLE_KEY_ID": "KEY1234567",
        "APPLE_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----",
        "APPLE_CLIENT_IDS": "com.example.app",
    })
    def test_returns_true_when_all_set(self):
        assert apple.is_configured() is True

    @patch.dict("os.environ", {
        "APPLE_TEAM_ID": "TEAM123456",
        "APPLE_KEY_ID": "",
        "APPLE_PRIVATE_KEY": "fake",
        "APPLE_CLIENT_IDS": "com.example.app",
    }, clear=False)
    def test_returns_false_when_key_id_empty(self):
        assert apple.is_configured() is False

    @patch.dict("os.environ", {
        "APPLE_TEAM_ID": "TEAM123456",
        "APPLE_KEY_ID": "KEY1234567",
        "APPLE_PRIVATE_KEY": "fake",
        "APPLE_CLIENT_IDS": "",
    }, clear=False)
    def test_returns_false_when_no_client_ids(self):
        assert apple.is_configured() is False

    @patch.dict("os.environ", {}, clear=True)
    def test_returns_false_when_nothing_set(self):
        assert apple.is_configured() is False


class TestClientIds:
    @patch.dict("os.environ", {"APPLE_CLIENT_IDS": "com.a,com.b, com.c "})
    def test_parses_csv(self):
        assert apple._client_ids() == ["com.a", "com.b", "com.c"]

    @patch.dict("os.environ", {"APPLE_CLIENT_IDS": ""})
    def test_empty_string(self):
        assert apple._client_ids() == []

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_env(self):
        assert apple._client_ids() == []

    @patch.dict("os.environ", {"APPLE_CLIENT_IDS": "  , , , "})
    def test_only_whitespace_commas(self):
        assert apple._client_ids() == []


# ---------------------------------------------------------------------------
# _client_secret
# ---------------------------------------------------------------------------


class TestClientSecret:
    @patch.dict("os.environ", {
        "APPLE_TEAM_ID": "TEAM123456",
        "APPLE_KEY_ID": "KEY1234567",
        "APPLE_PRIVATE_KEY": "fake-pem",
    })
    def test_builds_jwt(self):
        mock_jwt = MagicMock()
        mock_jwt.encode.return_value = "signed-token"
        with patch.dict("sys.modules", {"jwt": mock_jwt}):
            result = apple._client_secret("com.example.app")
        assert result == "signed-token"
        call_args = mock_jwt.encode.call_args
        payload = call_args[0][0]
        assert payload["iss"] == "TEAM123456"
        assert payload["sub"] == "com.example.app"
        assert payload["aud"] == "https://appleid.apple.com"
        assert call_args[1]["algorithm"] == "ES256"
        assert call_args[1]["headers"]["kid"] == "KEY1234567"


# ---------------------------------------------------------------------------
# exchange_code
# ---------------------------------------------------------------------------


class TestExchangeCode:
    async def test_returns_none_when_not_configured(self):
        with patch.object(apple, "is_configured", return_value=False):
            rt, cid = await apple.exchange_code("some-code")
        assert rt is None and cid is None

    async def test_returns_none_for_empty_code(self):
        with patch.object(apple, "is_configured", return_value=True):
            rt, cid = await apple.exchange_code("")
        assert rt is None and cid is None

    async def test_success_returns_refresh_token(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"refresh_token": "rt-abc"}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(apple, "is_configured", return_value=True), \
             patch.object(apple, "_client_ids", return_value=["com.a"]), \
             patch.object(apple, "_client_secret", return_value="secret"), \
             patch("core.apple.httpx.AsyncClient", return_value=mock_client):
            rt, cid = await apple.exchange_code("auth-code-123")

        assert rt == "rt-abc"
        assert cid == "com.a"

    async def test_tries_second_client_id_on_failure(self):
        fail_resp = MagicMock()
        fail_resp.status_code = 400
        fail_resp.text = "invalid_grant"

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"refresh_token": "rt-second"}

        mock_client = AsyncMock()
        mock_client.post.side_effect = [fail_resp, ok_resp]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(apple, "is_configured", return_value=True), \
             patch.object(apple, "_client_ids", return_value=["com.dev", "com.prod"]), \
             patch.object(apple, "_client_secret", return_value="s"), \
             patch("core.apple.httpx.AsyncClient", return_value=mock_client):
            rt, cid = await apple.exchange_code("code")

        assert rt == "rt-second"
        assert cid == "com.prod"

    async def test_all_client_ids_fail(self):
        fail_resp = MagicMock()
        fail_resp.status_code = 400
        fail_resp.text = "bad"

        mock_client = AsyncMock()
        mock_client.post.return_value = fail_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(apple, "is_configured", return_value=True), \
             patch.object(apple, "_client_ids", return_value=["com.a", "com.b"]), \
             patch.object(apple, "_client_secret", return_value="s"), \
             patch("core.apple.httpx.AsyncClient", return_value=mock_client):
            rt, cid = await apple.exchange_code("code")

        assert rt is None and cid is None

    async def test_exception_in_post_continues(self):
        """An exception for one client_id should not prevent trying the next."""
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"refresh_token": "rt-ok"}

        mock_client = AsyncMock()
        mock_client.post.side_effect = [httpx.TimeoutException("timeout"), ok_resp]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(apple, "is_configured", return_value=True), \
             patch.object(apple, "_client_ids", return_value=["com.a", "com.b"]), \
             patch.object(apple, "_client_secret", return_value="s"), \
             patch("core.apple.httpx.AsyncClient", return_value=mock_client):
            rt, cid = await apple.exchange_code("code")

        assert rt == "rt-ok"
        assert cid == "com.b"


# ---------------------------------------------------------------------------
# revoke
# ---------------------------------------------------------------------------


class TestRevoke:
    async def test_returns_false_when_not_configured(self):
        with patch.object(apple, "is_configured", return_value=False):
            assert await apple.revoke("rt", "cid") is False

    async def test_returns_false_for_empty_token(self):
        with patch.object(apple, "is_configured", return_value=True):
            assert await apple.revoke("", "cid") is False

    async def test_returns_false_for_empty_client_id(self):
        with patch.object(apple, "is_configured", return_value=True):
            assert await apple.revoke("rt", "") is False

    async def test_success_returns_true(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(apple, "is_configured", return_value=True), \
             patch.object(apple, "_client_secret", return_value="s"), \
             patch("core.apple.httpx.AsyncClient", return_value=mock_client):
            assert await apple.revoke("rt-123", "com.a") is True

    async def test_non_200_returns_false(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "invalid_token"

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(apple, "is_configured", return_value=True), \
             patch.object(apple, "_client_secret", return_value="s"), \
             patch("core.apple.httpx.AsyncClient", return_value=mock_client):
            assert await apple.revoke("rt-123", "com.a") is False

    async def test_exception_returns_false(self):
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("down")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(apple, "is_configured", return_value=True), \
             patch.object(apple, "_client_secret", return_value="s"), \
             patch("core.apple.httpx.AsyncClient", return_value=mock_client):
            assert await apple.revoke("rt-123", "com.a") is False
