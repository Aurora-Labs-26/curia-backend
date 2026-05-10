"""
core/db/migrations.py
DEPRECATED — Surreal-era migration runner.

Schema is now managed by Alembic. See alembic/versions/ and run:
    alembic upgrade head

scripts/setup_db.py invokes alembic for backward compatibility.
"""

from loguru import logger


async def run_migrations() -> None:
    """Kept for backward compatibility — directs callers to alembic."""
    logger.warning(
        "core/db/migrations.run_migrations() is deprecated. "
        "Use `alembic upgrade head` (or `python scripts/setup_db.py`) instead."
    )
