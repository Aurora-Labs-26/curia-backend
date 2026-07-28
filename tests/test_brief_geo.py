"""
tests/test_brief_geo.py — brief/geo.py's client-IP resolution and the
private-address guard. httpx is mocked; no real network call.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from brief import geo


def _request(xff: str | None = None, peer: str = "1.2.3.4"):
    headers = {"x-forwarded-for": xff} if xff is not None else {}
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=peer))


class TestClientIp:
    def test_no_proxy_uses_socket_peer(self):
        with patch.object(geo, "XFF_INDEX", 0):
            assert geo.client_ip(_request(peer="9.9.9.9")) == "9.9.9.9"

    def test_single_proxy_takes_rightmost(self):
        with patch.object(geo, "XFF_INDEX", 1):
            req = _request(xff="203.0.113.9")
            assert geo.client_ip(req) == "203.0.113.9"

    def test_cloudfront_and_alb_takes_second_from_right(self):
        """Prod chain: viewer -> CloudFront -> ALB. Each hop APPENDS, so the ALB
        sees "<viewer>, <cloudfront-edge>" — the viewer is 2nd from the right,
        not the first entry (which callers might assume, and which a client
        can freely spoof by prepending junk to its own header)."""
        with patch.object(geo, "XFF_INDEX", 2):
            req = _request(xff="203.0.113.9, 130.176.1.1")
            assert geo.client_ip(req) == "203.0.113.9"

    def test_spoofed_leading_entry_does_not_win(self):
        """A client-supplied X-Forwarded-For only prepends — CloudFront still
        appends the real viewer IP after it — so counting from the right stays
        correct even when the client tries to lie."""
        with patch.object(geo, "XFF_INDEX", 2):
            req = _request(xff="6.6.6.6, 203.0.113.9, 130.176.1.1")
            assert geo.client_ip(req) == "203.0.113.9"

    def test_fewer_hops_than_expected_does_not_guess(self):
        """Fewer entries than the configured chain length means this request
        didn't come through the expected proxy path (health check, direct ALB
        hit) — return unknown rather than picking a wrong entry."""
        with patch.object(geo, "XFF_INDEX", 2):
            req = _request(xff="203.0.113.9")
            assert geo.client_ip(req) == ""


class TestIsPublic:
    @pytest.mark.parametrize("ip", [
        "10.0.0.5", "172.16.0.1", "192.168.1.1", "127.0.0.1", "169.254.1.1",
    ])
    def test_rejects_private_and_special_ranges(self, ip):
        assert geo._is_public(ip) is False

    def test_accepts_public_ip(self):
        assert geo._is_public("8.8.8.8") is True

    def test_rejects_garbage(self):
        assert geo._is_public("not-an-ip") is False


class TestCityFromRequest:
    async def test_override_short_circuits_everything(self):
        """Docker Desktop's local port-forwarding presents an arbitrary PUBLIC
        IP as the peer (observed: a Google address resolving to a real but
        wrong city) — the override must win before any IP/lookup logic runs,
        so local dev never depends on the lookup succeeding correctly."""
        with patch.object(geo, "OVERRIDE_CITY", "Bengaluru"):
            assert await geo.city_from_request(_request(peer="172.217.113.4")) == "Bengaluru"

    async def test_private_ip_returns_empty_without_calling_api(self):
        with patch.object(geo, "OVERRIDE_CITY", ""):
            with patch("httpx.AsyncClient") as client_cls:
                result = await geo.city_from_request(_request(peer="10.0.0.5"))
        assert result == ""
        client_cls.assert_not_called()

    async def test_successful_lookup_returns_city(self):
        mock_resp = SimpleNamespace(status_code=200, json=lambda: {"city": "Pune"})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch.object(geo, "OVERRIDE_CITY", ""), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await geo.city_from_request(_request(peer="8.8.8.8"))
        assert result == "Pune"

    async def test_non_200_fails_soft(self):
        mock_resp = SimpleNamespace(status_code=429, json=lambda: {})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch.object(geo, "OVERRIDE_CITY", ""), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await geo.city_from_request(_request(peer="8.8.8.8"))
        assert result == ""

    async def test_network_error_fails_soft(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=RuntimeError("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch.object(geo, "OVERRIDE_CITY", ""), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await geo.city_from_request(_request(peer="8.8.8.8"))
        assert result == ""
