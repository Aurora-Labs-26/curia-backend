"""
core/storage/blob.py
Cloud blob storage abstraction — S3-compatible (AWS S3, Cloudflare R2, MinIO, etc.)
and local filesystem fallback for dev.

Usage:
    from core.storage import upload_blob, get_blob_url

    url = await upload_blob(data=image_bytes, key="images/abc.jpg", content_type="image/jpeg")
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from loguru import logger


def get_storage_backend() -> str:
    return os.getenv("CURIA_STORAGE_BACKEND", "local")


async def upload_blob(
    data: bytes,
    key: str,
    content_type: str = "application/octet-stream",
) -> str:
    backend = get_storage_backend()
    if backend == "s3":
        return await _upload_s3(data, key, content_type)
    return await _upload_local(data, key)


def get_blob_url(key: str) -> str:
    backend = get_storage_backend()
    if backend == "s3":
        bucket = os.getenv("CURIA_S3_BUCKET", "curia-assets")
        region = os.getenv("CURIA_S3_REGION", "us-east-1")
        endpoint = os.getenv("CURIA_S3_ENDPOINT")
        if endpoint:
            return f"{endpoint.rstrip('/')}/{bucket}/{key}"
        return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
    base = os.getenv("CURIA_STORAGE_LOCAL_DIR", "data/blobs")
    return f"file://{Path(base).resolve()}/{key}"


# ---------------------------------------------------------------------------
# S3-compatible backend
# ---------------------------------------------------------------------------


async def _upload_s3(data: bytes, key: str, content_type: str) -> str:
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise RuntimeError("boto3 is required for S3 storage — pip install boto3")

    bucket = os.getenv("CURIA_S3_BUCKET", "curia-assets")
    region = os.getenv("CURIA_S3_REGION", "us-east-1")
    endpoint = os.getenv("CURIA_S3_ENDPOINT")

    kwargs: dict = {"region_name": region}
    if endpoint:
        kwargs["endpoint_url"] = endpoint

    access_key = os.getenv("CURIA_S3_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("CURIA_S3_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key

    import asyncio

    def _put():
        client = boto3.client("s3", **kwargs)
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    await asyncio.to_thread(_put)
    url = get_blob_url(key)
    logger.info(f"[storage] uploaded {len(data)} bytes → s3://{bucket}/{key}")
    return url


# ---------------------------------------------------------------------------
# Local filesystem fallback (dev)
# ---------------------------------------------------------------------------


async def _upload_local(data: bytes, key: str) -> str:
    base = Path(os.getenv("CURIA_STORAGE_LOCAL_DIR", "data/blobs"))
    dest = base / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    url = f"file://{dest.resolve()}"
    logger.info(f"[storage] saved {len(data)} bytes → {dest}")
    return url
