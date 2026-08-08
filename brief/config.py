"""brief/config — env shim for the ported daily-brief services."""
import os


class _Settings:
    @property
    def WEATHERAPI_KEY(self) -> str:
        return os.getenv("WEATHERAPI_KEY", "")

    @property
    def OPENWEATHER_API_KEY(self) -> str:
        return os.getenv("OPENWEATHER_API_KEY", "")

    @property
    def WEATHER_PROVIDER(self) -> str:
        """"openweather" (default) or "weatherapi". WeatherAPI.com has shown
        inaccurate conditions for some users; OpenWeatherMap is the new
        default with WeatherAPI kept as a configurable fallback."""
        return os.getenv("WEATHER_PROVIDER", "openweather")


settings = _Settings()
