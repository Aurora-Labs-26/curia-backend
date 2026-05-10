"""
api/main.py
FastAPI application entrypoint.

Run locally:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

In docker-compose this is the `api` service's command.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from loguru import logger

from api.routes import admin, episodes, health, ideas, me, sources
from core.db.connection import close_pool, get_pool

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — warm the asyncpg pool early so first request is fast and any
    # connection problem surfaces at boot, not on first request.
    await get_pool()
    logger.info("[api] startup complete")
    try:
        yield
    finally:
        await close_pool()
        logger.info("[api] shutdown complete")


app = FastAPI(
    title="Curia",
    version="0.1.0",
    description="Personal reading archive → original podcast episodes.",
    lifespan=lifespan,
)

# Routers — order is cosmetic; FastAPI resolves by path
app.include_router(health.router, tags=["health"])
app.include_router(me.router, tags=["me"])
app.include_router(sources.router, tags=["sources"])
app.include_router(ideas.router, tags=["ideas"])
app.include_router(episodes.router, tags=["episodes"])
app.include_router(admin.router)   # QA-only; gated inside via Depends(qa_required)
