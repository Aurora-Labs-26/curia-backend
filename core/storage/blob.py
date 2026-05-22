"""
core/storage/blob.py
Cloud blob storage abstraction — S3-compatible (AWS S3, Cloudflare R2, MinIO, etc.)
and local filesystem fallback for dev.

Usage:
    from core.storage import upload_blob, get_blob_url

    url = await upload_blob(data=image_bytes, key="images/abc.jpg", content_type="image/jpeg")

Env vars:
    CURIA_STORAGE_BACKEND   "s3" | "local" (default: "local")
    CURIA_S3_BUCKET         bucket name (default: "curia-assets")
    CURIA_S3_REGION         region (default: "us-east-1")
    CURIA_S3_ENDPOINT       endpoint URL for R2/MinIO (e.g. https://<account>.r2.cloudflarestorage.com)
    CURIA_S3_ACCESS_KEY     access key ID (falls back to AWS_ACCESS_KEY_ID)
    CURIA_S3_SECRET_KEY     secret key (falls back to AWS_SECRET_ACCESS_KEY)
"""

from __future__ import annotations

import os
from pathlib import Path

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


async def upload_file(
    file_path: str,
    key: str,
    content_type: str = "application/octet-stream",
) -> str:
    """Upload a file from disk to blob storage. Returns the storage URL/key."""
    backend = get_storage_backend()
    if backend == "s3":
        return await _upload_s3_file(file_path, key, content_type)
    data = Path(file_path).read_bytes()
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


def generate_presigned_url(key: str, expires_in: int = 3600) -> str | None:
    """Generate a presigned download URL for a private S3/R2 object. Returns None if not using S3."""
    if get_storage_backend() != "s3":
        return None

    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        return None

    bucket = os.getenv("CURIA_S3_BUCKET", "curia-assets")
    region = os.getenv("CURIA_S3_REGION", "us-east-1")
    endpoint = os.getenv("CURIA_S3_ENDPOINT")

    kwargs: dict = {
        "region_name": region,
        "config": Config(signature_version="s3v4"),
    }
    if endpoint:
        kwargs["endpoint_url"] = endpoint

    access_key = os.getenv("CURIA_S3_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("CURIA_S3_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key

    client = boto3.client("s3", **kwargs)
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_in,
    )


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

    kwargs: dict = {
        "region_name": region,
        "config": Config(signature_version="s3v4"),
    }
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


async def _upload_s3_file(file_path: str, key: str, content_type: str) -> str:
    """Upload a file from disk to S3-compatible storage (avoids loading entire file into memory)."""
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise RuntimeError("boto3 is required for S3 storage — pip install boto3")

    bucket = os.getenv("CURIA_S3_BUCKET", "curia-assets")
    region = os.getenv("CURIA_S3_REGION", "us-east-1")
    endpoint = os.getenv("CURIA_S3_ENDPOINT")

    kwargs: dict = {
        "region_name": region,
        "config": Config(signature_version="s3v4"),
    }
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
        client.upload_file(
            file_path,
            bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )

    await asyncio.to_thread(_put)
    url = get_blob_url(key)
    file_size = Path(file_path).stat().st_size
    logger.info(f"[storage] uploaded file {file_size} bytes → s3://{bucket}/{key}")
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
