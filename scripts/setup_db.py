"""
scripts/setup_db.py
Run once to set up the SurrealDB schema.
Usage: python scripts/setup_db.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.db.migrations import run_migrations

if __name__ == "__main__":
    asyncio.run(run_migrations())
    print("DB setup complete.")
