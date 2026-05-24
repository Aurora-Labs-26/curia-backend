"""POST /waitlist — public email capture for landing page."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

from core.db.connection import db_execute, db_fetchrow

router = APIRouter()


class WaitlistRequest(BaseModel):
    email: EmailStr
    source: str | None = None


@router.post("/waitlist", status_code=201)
async def join_waitlist(body: WaitlistRequest) -> JSONResponse:
    existing = await db_fetchrow(
        "SELECT id FROM waitlist WHERE email = $email",
        {"email": body.email},
    )
    if existing:
        return JSONResponse(status_code=200, content={"status": "already_registered"})

    await db_execute(
        "INSERT INTO waitlist (email, source) VALUES ($email, $source)",
        {"email": body.email, "source": body.source},
    )
    return JSONResponse(status_code=201, content={"status": "ok"})
