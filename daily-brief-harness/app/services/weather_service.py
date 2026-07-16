"""
WeatherAPI.com integration — current conditions + local time for a given
location string. Grounds the spoken intro segment in real-world context
(see app/templates/prompts/intro_outro.py).
"""
import logging
from typing import Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def get_weather_and_local_time(location: str) -> Tuple[str, str]:
    """Returns (weather, local_time) for `location`, or ("", "") if the
    WeatherAPI key isn't configured, no location was given, or the request
    fails for any reason."""
    if not settings.WEATHERAPI_KEY or not location:
        return "", ""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://api.weatherapi.com/v1/current.json",
                params={"key": settings.WEATHERAPI_KEY, "q": location, "aqi": "no"},
            )
            if resp.status_code != 200:
                logger.warning(f"WeatherAPI returned {resp.status_code} for '{location}': {resp.text}")
                return "", ""
            data = resp.json()
            current = data["current"]
            weather = f"{current['condition']['text']}, {current['temp_c']}°C"
            local_time = data.get("location", {}).get("localtime", "")
            return weather, local_time
    except Exception as e:
        logger.warning(f"Weather/local time fetch failed for '{location}': {e}")
        return "", ""
