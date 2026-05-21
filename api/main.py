"""
api/main.py
FastAPI application entrypoint.

Run locally:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

In docker-compose this is the `api` service's command.
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

from api.routes import admin, auth, episodes, generate_from_source, health, ideas, jobs, me, sources, stream
from core.db.connection import close_pool, get_pool

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.time()
        response = await call_next(request)
        elapsed = time.time() - start
        logger.info(
            f"HTTP {request.method} {request.url.path} -> {response.status_code} ({elapsed:.3f}s)"
        )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Logging setup — must happen before anything else
    from core.logging import setup_logging
    from core.prompt_watcher import init_prompt_hashes
    from pathlib import Path
    PROMPTS_DIR = Path(os.getenv(
        "CURIA_PROMPTS_DIR",
        str(Path(__file__).resolve().parent.parent / "prompts"),
    ))

    setup_logging(service="api")
    init_prompt_hashes(PROMPTS_DIR)

    logger.info(f"[api] DATABASE_URL set: {bool(os.getenv('DATABASE_URL'))}")
    logger.info(f"[api] PORT={os.getenv('PORT', 'not set')}")

    try:
        await get_pool()
        logger.info("[api] DB pool ready")
    except Exception as e:
        logger.warning(f"[api] pool warmup failed ({e}); will retry on first request")

    from core.firebase import init_firebase
    init_firebase()

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CURIA_CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestLogMiddleware)

# Routers — order is cosmetic; FastAPI resolves by path
app.include_router(health.router, tags=["health"])
app.include_router(auth.router, tags=["auth"])
app.include_router(me.router, tags=["me"])
app.include_router(sources.router, tags=["sources"])
app.include_router(ideas.router, tags=["ideas"])
app.include_router(episodes.router, tags=["episodes"])
app.include_router(generate_from_source.router, tags=["generate"])
app.include_router(jobs.router, tags=["jobs"])
app.include_router(stream.router, tags=["streaming"])
app.include_router(admin.router)   # QA-only; gated inside via Depends(qa_required)
