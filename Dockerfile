# syntax=docker/dockerfile:1.7
# ─────────────────────────────────────────────────────────────────────────────
# Curia — single image, two run modes (api / worker).
#
# docker-compose runs:
#   api    : uvicorn api.main:app --host 0.0.0.0 --port 8000
#   worker : python -m worker.main
#
# Both services use this same image; commands are overridden in docker-compose.yml.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps:
#   ffmpeg     — pydub stitches MP3s; needs an ffmpeg binary at runtime
#   libpq-dev  — keeps asyncpg happy on slim images for some wheel paths
#   build deps — temporary; removed in the cleanup step to keep the image small
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libpq-dev \
        gcc \
        g++ \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cache layer separately from source).
# We need pyproject.toml + a stub package layout so pip install -e .[api] resolves.
COPY pyproject.toml README.md /app/
RUN mkdir -p core intelligence studio commands api worker config alembic \
    && touch core/__init__.py intelligence/__init__.py studio/__init__.py \
              commands/__init__.py api/__init__.py worker/__init__.py
RUN pip install -e ".[api]"

# Now copy the real source. Subsequent builds with code-only changes hit this layer.
COPY . /app

# Default to API; docker-compose overrides for the worker service.
# Railway injects PORT dynamically; fall back to 8000 for local dev.
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
