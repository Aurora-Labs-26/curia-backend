"""brief/config — env shim for the ported daily-brief services."""
import os


class _Settings:
    @property
    def WEATHERAPI_KEY(self) -> str:
        return os.getenv("WEATHERAPI_KEY", "")


settings = _Settings()
