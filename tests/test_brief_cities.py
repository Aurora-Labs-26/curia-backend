"""
tests/test_brief_cities.py — the geocoder-backed city layer that replaced
IP-geo (brief/cities.py): search parsing, resolve matching order, failure
posture (raise, never guess), and the local-query term helper. httpx mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brief import cities


def _client(resp=None, exc=None):
    c = MagicMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.get = AsyncMock(return_value=resp, side_effect=exc)
    return c








class TestCityQueryTerm:
    def test_bare_city_from_canonical_display(self):
        assert cities.city_query_term("Mumbai, Maharashtra, India") == "Mumbai"
        assert cities.city_query_term("Singapore") == "Singapore"


class TestNewsGeoForCountry:
    def test_known_code_wins_over_string_sniffing(self):
        from brief.news import news_service
        assert news_service.geo_for_country("GB", "anything") == ("GB", "en-GB", "GB:en")

    def test_unknown_code_falls_back_to_token_sniff(self):
        from brief.news import news_service
        assert news_service.geo_for_country("", "Mumbai, Maharashtra, India") == ("IN", "en-IN", "IN:en")

    def test_berlin_no_longer_resolves_to_india(self):
        from brief.news import news_service
        assert news_service.geo_for_country("", "Berlin, Germany")[0] != "IN"
        assert news_service.geo_for_country("", "China")[0] != "IN"


class TestReverseGeocode:
    def _resp(self, addr, status=200):
        r = MagicMock(status_code=status)
        r.json.return_value = {"address": addr}
        return r

    async def test_city_fix_resolves_with_device_coords_kept(self):
        addr = {"city": "Mumbai", "state": "Maharashtra", "country": "India",
                "country_code": "in"}
        with patch("brief.cities.httpx.AsyncClient",
                   return_value=_client(self._resp(addr))):
            m = await cities.reverse_geocode(19.076, 72.877)
        assert m["display"] == "Mumbai, Maharashtra, India"
        assert m["country_code"] == "IN"
        assert (m["latitude"], m["longitude"]) == (19.076, 72.877)   # device, not city-center

    async def test_town_and_village_fallback_order(self):
        addr = {"village": "Khardi", "state": "Maharashtra", "country": "India",
                "country_code": "in"}
        with patch("brief.cities.httpx.AsyncClient",
                   return_value=_client(self._resp(addr))):
            m = await cities.reverse_geocode(19.6, 73.3)
        assert m["name"] == "Khardi"

    async def test_open_ocean_returns_none(self):
        with patch("brief.cities.httpx.AsyncClient",
                   return_value=_client(self._resp({"country": "nowhere"}))):
            assert await cities.reverse_geocode(0.0, -140.0) is None

    async def test_outage_raises_never_guesses(self):
        with patch("brief.cities.httpx.AsyncClient",
                   return_value=_client(exc=RuntimeError("dns"))):
            with pytest.raises(cities.GeocoderUnavailable):
                await cities.reverse_geocode(19.0, 72.8)

    async def test_non_200_raises_not_none(self):
        """A rate-limited/erroring reverse geocoder must surface as 503-retry,
        not as 'we could not resolve a city' (caught by a survived mutant)."""
        with patch("brief.cities.httpx.AsyncClient",
                   return_value=_client(self._resp({}, status=429))):
            with pytest.raises(cities.GeocoderUnavailable):
                await cities.reverse_geocode(19.0, 72.8)
