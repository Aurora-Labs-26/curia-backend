"""
Weather + local-time lookup — grounds the spoken intro segment in real-world
context (see app/templates/prompts/intro_outro.py).

Provider-agnostic: OpenWeatherMap is the default (brief/config.py
WEATHER_PROVIDER), WeatherAPI.com is kept as a configurable fallback —
WeatherAPI has shown inaccurate conditions for some users.

Coordinate-first: when the caller has latitude/longitude (captured at the
geocoder match in brief/cities.py and persisted on harness.users.location),
both providers are queried by coordinate instead of re-searching the
free-text location string. Free-text search means the weather provider can
independently resolve to a different same-named place than the one the
city picker/geocoder already confirmed — coordinates remove that ambiguity.
Falls back to the free-text `location` string when no coordinates are
available (e.g. rows saved before this change).
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Optional, Tuple

import httpx
from loguru import logger

from brief.config import settings


async def _openweather(lat: Optional[float], lon: Optional[float], location: str) -> Tuple[str, str]:
    params = {"appid": settings.OPENWEATHER_API_KEY, "units": "metric"}
    if lat is not None and lon is not None:
        params["lat"] = lat
        params["lon"] = lon
    else:
        params["q"] = location
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get("https://api.openweathermap.org/data/2.5/weather", params=params)
        if resp.status_code != 200:
            logger.warning(f"OpenWeatherMap returned {resp.status_code} for '{location}': {resp.text}")
            return "", ""
        data = resp.json()
        condition = (data.get("weather") or [{}])[0].get("description", "").capitalize()
        temp_c = data.get("main", {}).get("temp")
        if not condition or temp_c is None:
            return "", ""
        weather = f"{condition}, {temp_c}°C"
        tz_offset_s = data.get("timezone", 0)
        local_dt = datetime.now(dt_timezone.utc) + timedelta(seconds=tz_offset_s)
        local_time = local_dt.strftime("%Y-%m-%d %H:%M")
        return weather, local_time


async def _weatherapi(lat: Optional[float], lon: Optional[float], location: str) -> Tuple[str, str]:
    q = f"{lat},{lon}" if lat is not None and lon is not None else location
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(
            "https://api.weatherapi.com/v1/current.json",
            params={"key": settings.WEATHERAPI_KEY, "q": q, "aqi": "no"},
        )
        if resp.status_code != 200:
            logger.warning(f"WeatherAPI returned {resp.status_code} for '{q}': {resp.text}")
            return "", ""
        data = resp.json()
        current = data["current"]
        weather = f"{current['condition']['text']}, {current['temp_c']}°C"
        local_time = data.get("location", {}).get("localtime", "")
        return weather, local_time


_PROVIDERS = {"openweather": _openweather, "weatherapi": _weatherapi}


async def get_weather_and_local_time(
    location: str, latitude: Optional[float] = None, longitude: Optional[float] = None
) -> Tuple[str, str]:
    """Returns (weather, local_time) for `location` (+ optional lat/lon), or
    ("", "") if no provider is configured, no location was given, or the
    request fails for any reason."""
    if not location:
        return "", ""
    provider = _PROVIDERS.get(settings.WEATHER_PROVIDER, _openweather)
    key = settings.OPENWEATHER_API_KEY if provider is _openweather else settings.WEATHERAPI_KEY
    if not key:
        # Fall back to whichever provider does have a key configured, so a
        # WEATHER_PROVIDER=openweather deploy without OPENWEATHER_API_KEY set
        # doesn't silently lose weather entirely if WEATHERAPI_KEY is present.
        if settings.OPENWEATHER_API_KEY:
            provider = _openweather
        elif settings.WEATHERAPI_KEY:
            provider = _weatherapi
        else:
            return "", ""
    try:
        return await provider(latitude, longitude, location)
    except Exception as e:
        logger.warning(f"Weather/local time fetch failed for '{location}': {e}")
        return "", ""
