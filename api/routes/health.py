"""GET /health — liveness probe (no auth)."""

from fastapi import APIRouter

from core.db.connection import db_fetchrow

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    db_ok = False
    try:
        row = await db_fetchrow("SELECT 1 AS ok", {})
        db_ok = bool(row and row.get("ok") == 1)
    except Exception:
        db_ok = False
    return {"status": "ok", "db": db_ok}
