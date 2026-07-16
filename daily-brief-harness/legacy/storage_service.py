"""
R2/S3-compatible blob storage for brief audio files.
"""

from __future__ import annotations

import asyncio
import os


async def upload_audio(data: bytes, key: str) -> str:
    """Upload WAV bytes to R2, return public URL."""
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise RuntimeError("boto3 required — pip install boto3")

    bucket = os.environ["CURIA_S3_BUCKET"]
    endpoint = os.environ.get("CURIA_S3_ENDPOINT")
    access_key = os.environ["CURIA_S3_ACCESS_KEY"]
    secret_key = os.environ["CURIA_S3_SECRET_KEY"]
    region = os.environ.get("CURIA_S3_REGION", "auto")

    kwargs: dict = {
        "region_name": region,
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "config": Config(signature_version="s3v4"),
    }
    if endpoint:
        kwargs["endpoint_url"] = endpoint

    def _put() -> None:
        client = boto3.client("s3", **kwargs)
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType="audio/wav",
        )

    await asyncio.to_thread(_put)

    if endpoint:
        return f"{endpoint.rstrip('/')}/{bucket}/{key}"
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
