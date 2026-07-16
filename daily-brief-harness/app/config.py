import os
from dotenv import load_dotenv

# Load env variables from a .env file if it exists
load_dotenv()

class Settings:
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    WEATHERAPI_KEY: str = os.getenv("WEATHERAPI_KEY", "")
    GOOGLE_SERVICE_ACCOUNT_FILE: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "")
    GOOGLE_CALENDAR_ID: str = os.getenv("GOOGLE_CALENDAR_ID", "")
    PORT: int = int(os.getenv("PORT", 8000))
    HOST: str = os.getenv("HOST", "127.0.0.1")
    DEBUG: bool = os.getenv("DEBUG", "true").lower() in ("true", "1", "yes")

    # Optional shared HTTP Basic Auth in front of the whole app (see app/main.py).
    # Leave both unset to disable — used to gate temporary public test deployments.
    BASIC_AUTH_USER: str = os.getenv("BASIC_AUTH_USER", "")
    BASIC_AUTH_PASS: str = os.getenv("BASIC_AUTH_PASS", "")

    # Local Postgres for the harness's own pre-opt/per-user-brief cache schema
    # (`harness.*` tables — see db/001_schema.sql). Separate, unrelated
    # concern from the retired production pipeline's `legacy/db_service.py`
    # `DATABASE_URL` (old `public.daily_briefs` table) — that module reads
    # os.environ directly and is untouched by this setting.
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "postgresql://curia:curia_dev@localhost:5433/curia_harness"
    )

settings = Settings()
