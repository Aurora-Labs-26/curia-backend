"""
brief/cities.py
Geocoder-backed city search + validation — the replacement for IP
geolocation (brief/geo.py, deleted). Design decision 2026-08-03: IP-geo on
carrier IPs guesses the wrong city too often to be the silent source of
truth, and the free-text correction path accepted anything ("timbaktu").
Now the ONLY way a city enters the system is through this geocoder:

  - GET /brief/cities?q=   -> search_cities()   typeahead for the picker
  - PUT /brief/preferences -> resolve_city()    validates + normalizes any
                              submitted location_name at the API edge

Backed by Open-Meteo's geocoding API: free, keyless, returns name/admin1/
country/country_code/timezone per match. The country_code is what finally
kills news.py's keyword country-sniffing for local news (which
substring-matched "in" — so "Berlin" resolved to the India edition).

Failure posture: search/resolve RAISE on geocoder outage — callers map that
to 503 ("try again"), because storing an unverified city would silently
recreate the exact problem this module exists to prevent. A save without a
city is never blocked.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

GEOCODE_URL = os.getenv(
    "CURIA_GEOCODE_URL", "https://geocoding-api.open-meteo.com/v1/search"
)
REVERSE_URL = os.getenv(
    "CURIA_REVERSE_GEOCODE_URL", "https://nominatim.openstreetmap.org/reverse"
)
TIMEOUT_S = 6
_UA = {"User-Agent": "curia-backend/1.0 (brief location resolution)"}


class GeocoderUnavailable(RuntimeError):
    """Open-Meteo unreachable/erroring — callers return 503, never a guess."""


def _display(r: Dict[str, Any]) -> str:
    """Canonical stored/display form: "Mumbai, Maharashtra, India"."""
    return ", ".join(
        p for p in (r.get("name"), r.get("admin1"), r.get("country")) if p
    )


async def search_cities(q: str, count: int = 8) -> List[Dict[str, Any]]:
    """Typeahead matches for a partial city name. Each entry:
    {"display", "name", "country_code", "timezone"}. Raises
    GeocoderUnavailable on outage; returns [] for no matches."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            resp = await client.get(
                GEOCODE_URL,
                params={"name": q, "count": count, "language": "en", "format": "json"},
            )
        if resp.status_code != 200:
            raise GeocoderUnavailable(f"geocoder returned {resp.status_code}")
        results = (resp.json() or {}).get("results") or []
    except GeocoderUnavailable:
        raise
    except Exception as e:
        logger.warning(f"[brief.cities] geocoder failed for {q!r}: {e}")
        raise GeocoderUnavailable(str(e))
    return [
        {
            "display": _display(r),
            "name": r.get("name") or "",
            "country_code": (r.get("country_code") or "").upper(),
            "timezone": r.get("timezone") or "",
            "latitude": r.get("latitude"),
            "longitude": r.get("longitude"),
        }
        for r in results
    ]


async def resolve_city(q: str) -> Optional[Dict[str, Any]]:
    """Validate + normalize one submitted city string. Match preference:
    exact display ("Mumbai, Maharashtra, India" round-trips from the picker),
    then exact city name, then the geocoder's top match (typo forgiveness:
    "mumbi" -> Mumbai). None = not a place -> caller 422s. Search is done on
    the city part only — the geocoder doesn't understand full display
    strings as queries."""
    city_part = q.split(",")[0].strip()
    if not city_part:
        return None
    matches = await search_cities(city_part)
    if not matches:
        return None
    ql = q.strip().lower()
    for m in matches:
        if m["display"].lower() == ql:
            return m
    for m in matches:
        if m["name"].lower() == ql:
            return m
    return matches[0]


async def reverse_geocode(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Device GPS fix -> the same shape resolve_city returns, so the two
    entry paths (picker and location permission) store identically-shaped,
    verified places. Nominatim (keyless, full place names, zoom=10 = city
    granularity); raises GeocoderUnavailable on outage — a GPS save either
    stores a real resolved place or tells the client to retry, never a guess.
    Returns None only when the fix resolves to no city-like locality at all
    (open ocean, wilderness)."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=_UA) as client:
            resp = await client.get(REVERSE_URL, params={
                "lat": lat, "lon": lon, "format": "jsonv2", "zoom": 10,
                "accept-language": "en",
            })
        if resp.status_code != 200:
            raise GeocoderUnavailable(f"reverse geocoder returned {resp.status_code}")
        addr = (resp.json() or {}).get("address") or {}
    except GeocoderUnavailable:
        raise
    except Exception as e:
        logger.warning(f"[brief.cities] reverse geocode failed for {lat},{lon}: {e}")
        raise GeocoderUnavailable(str(e))

    city = next((addr[k] for k in
                 ("city", "town", "village", "municipality", "county") if addr.get(k)), "")
    if not city:
        return None
    display = ", ".join(p for p in (city, addr.get("state"), addr.get("country")) if p)
    return {
        "display": display,
        "name": city,
        "country_code": (addr.get("country_code") or "").upper(),
        "timezone": "",           # reverse path: timezone stays the client's own
        "latitude": lat,          # DEVICE coords, deliberately more precise
        "longitude": lon,         # than any gazetteer city-center
    }


def city_query_term(location_name: str) -> str:
    """The bare city for Google News local queries — searching the full
    quoted display triple ('"Mumbai, Maharashtra, India"') would demand an
    exact phrase match no article contains."""
    return location_name.split(",")[0].strip()
