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


def _resp(results, status=200):
    r = MagicMock(status_code=status)
    r.json.return_value = {"results": results}
    return r


MUMBAI = {"name": "Mumbai", "admin1": "Maharashtra", "country": "India",
          "country_code": "IN", "timezone": "Asia/Kolkata"}
MUMBAI_US = {"name": "Mumbai", "admin1": "Ohio", "country": "United States",
             "country_code": "US", "timezone": "America/New_York"}


class TestSearchCities:
    async def test_parses_and_formats_display(self):
        with patch("brief.cities.httpx.AsyncClient", return_value=_client(_resp([MUMBAI]))):
            out = await cities.search_cities("mumbai")
        assert out == [{"display": "Mumbai, Maharashtra, India", "name": "Mumbai",
                        "country_code": "IN", "timezone": "Asia/Kolkata",
                        "latitude": None, "longitude": None}]

    async def test_display_skips_missing_admin1(self):
        row = {"name": "Singapore", "country": "Singapore", "country_code": "SG",
               "timezone": "Asia/Singapore"}
        with patch("brief.cities.httpx.AsyncClient", return_value=_client(_resp([row]))):
            out = await cities.search_cities("singapore")
        assert out[0]["display"] == "Singapore, Singapore"

    async def test_no_matches_returns_empty(self):
        with patch("brief.cities.httpx.AsyncClient", return_value=_client(_resp([]))):
            assert await cities.search_cities("zzzz") == []

    async def test_outage_raises_never_guesses(self):
        with patch("brief.cities.httpx.AsyncClient",
                   return_value=_client(exc=RuntimeError("dns"))):
            with pytest.raises(cities.GeocoderUnavailable):
                await cities.search_cities("mumbai")

    async def test_non_200_raises(self):
        with patch("brief.cities.httpx.AsyncClient",
                   return_value=_client(_resp([], status=500))):
            with pytest.raises(cities.GeocoderUnavailable):
                await cities.search_cities("mumbai")


class TestResolveCity:
    async def test_exact_display_roundtrip_wins_over_top_match(self):
        with patch.object(cities, "search_cities",
                          AsyncMock(return_value=[
                              {"display": "Mumbai, Ohio, United States", "name": "Mumbai",
                               "country_code": "US", "timezone": "America/New_York"},
                              {"display": "Mumbai, Maharashtra, India", "name": "Mumbai",
                               "country_code": "IN", "timezone": "Asia/Kolkata"},
                          ])):
            m = await cities.resolve_city("Mumbai, Maharashtra, India")
        assert m["country_code"] == "IN"

    async def test_bare_name_takes_top_match(self):
        with patch.object(cities, "search_cities",
                          AsyncMock(return_value=[
                              {"display": "Mumbai, Maharashtra, India", "name": "Mumbai",
                               "country_code": "IN", "timezone": "Asia/Kolkata"}])) as sc:
            m = await cities.resolve_city("mumbai")
        assert m["display"] == "Mumbai, Maharashtra, India"
        sc.assert_awaited_once_with("mumbai")

    async def test_search_uses_city_part_of_display_string(self):
        with patch.object(cities, "search_cities",
                          AsyncMock(return_value=[])) as sc:
            await cities.resolve_city("Pune, Maharashtra, India")
        sc.assert_awaited_once_with("Pune")

    async def test_unknown_place_returns_none(self):
        with patch.object(cities, "search_cities", AsyncMock(return_value=[])):
            assert await cities.resolve_city("timbaktuuu") is None

    async def test_empty_string_returns_none_without_search(self):
        with patch.object(cities, "search_cities", AsyncMock()) as sc:
            assert await cities.resolve_city("  ") is None
        sc.assert_not_awaited()


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
