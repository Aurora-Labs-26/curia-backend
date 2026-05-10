"""
scripts/setup_db.py
Run schema migrations against Postgres via Alembic.

Usage:
    python scripts/setup_db.py
or equivalently:
    alembic upgrade head

Reads DATABASE_URL from .env (default: postgresql://curia:curia@localhost:5432/curia).
"""

import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent

if __name__ == "__main__":
    cmd = ["alembic", "upgrade", "head"]
    print(f"Running: {' '.join(cmd)}  (cwd={ROOT})")
    result = subprocess.run(cmd, cwd=ROOT)
    sys.exit(result.returncode)
