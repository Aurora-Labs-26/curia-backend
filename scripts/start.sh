#!/bin/bash
set -e
echo "[start] fixing alembic heads..."
python scripts/fix_alembic_heads.py
echo "[start] running migrations..."
alembic upgrade 0028_daily_briefs
echo "[start] starting uvicorn..."
exec uvicorn api.main:app --host 0.0.0.0 --port 8000
