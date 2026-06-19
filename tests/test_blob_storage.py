"""
tests/test_blob_storage.py
Unit tests for core/storage/blob.py — storage backend routing, URL generation, delete.
All I/O mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.storage.blob as blob


# ---------------------------------------------------------------------------
# get_storage_backend / get_blob_url
# ---------------------------------------------------------------------------


class TestGetStorageBackend:
    @patch.dict("os.environ", {}, clear=True)
    def test_defaults_to_local(self):
        assert blob.get_storage_backend() == "local"

    @patch.dict("os.environ", {"CURIA_STORAGE_BACKEND": "s3"})
    def test_returns_s3(self):
        assert blob.get_storage_backend() == "s3"


class TestGetBlobUrl:
    @patch.dict("os.environ", {"CURIA_STORAGE_BACKEND": "s3", "CURIA_S3_BUCKET": "my-bucket", "CURIA_S3_REGION": "eu-west-1"})
    def test_s3_url_without_endpoint(self):
        url = blob.get_blob_url("audio/ep.mp3")
        assert url == "https://my-bucket.s3.eu-west-1.amazonaws.com/audio/ep.mp3"

    @patch.dict("os.environ", {
        "CURIA_STORAGE_BACKEND": "s3",
        "CURIA_S3_BUCKET": "b",
        "CURIA_S3_ENDPOINT": "https://r2.example.com/",
    })
    def test_s3_url_with_custom_endpoint(self):
        url = blob.get_blob_url("k")
        assert url == "https://r2.example.com/b/k"

    @patch.dict("os.environ", {"CURIA_STORAGE_BACKEND": "local"})
    def test_local_url(self):
        url = blob.get_blob_url("audio/ep.mp3")
        assert url.startswith("file://")
        assert "audio/ep.mp3" in url


# ---------------------------------------------------------------------------
# delete_blobs
# ---------------------------------------------------------------------------


class TestDeleteBlobs:
    async def test_empty_keys_returns_zero(self):
        assert await blob.delete_blobs([]) == 0

    async def test_filters_none_keys(self):
        assert await blob.delete_blobs([None, "", None]) == 0

    @patch.dict("os.environ", {"CURIA_STORAGE_BACKEND": "local", "CURIA_STORAGE_LOCAL_DIR": "/tmp/test-blobs"})
    async def test_local_delete(self):
        with patch("asyncio.to_thread", new=AsyncMock()) as mock_thread:
            count = await blob.delete_blobs(["a.mp3", "b.mp3"])
        assert count == 2
        mock_thread.assert_awaited_once()

    @patch.dict("os.environ", {
        "CURIA_STORAGE_BACKEND": "s3",
        "CURIA_S3_BUCKET": "b",
        "CURIA_S3_REGION": "us-east-1",
    })
    async def test_s3_delete_calls_boto(self):
        mock_client = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_client

        with patch("asyncio.to_thread", new=AsyncMock()) as mock_thread, \
             patch.dict("sys.modules", {"boto3": mock_boto3, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            count = await blob.delete_blobs(["key1", "key2"])
        assert count == 2


# ---------------------------------------------------------------------------
# generate_presigned_url
# ---------------------------------------------------------------------------


class TestGeneratePresignedUrl:
    @patch.dict("os.environ", {"CURIA_STORAGE_BACKEND": "local"})
    def test_returns_none_for_local(self):
        assert blob.generate_presigned_url("key") is None

    @patch.dict("os.environ", {
        "CURIA_STORAGE_BACKEND": "s3",
        "CURIA_S3_BUCKET": "b",
        "CURIA_S3_REGION": "us-east-1",
    })
    def test_returns_url_for_s3(self):
        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = "https://presigned.example.com/key"

        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_client

        with patch.dict("sys.modules", {"boto3": mock_boto3, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            import importlib
            # Re-import to pick up mocked boto3
            url = blob.generate_presigned_url("audio/ep.mp3", expires_in=600)
        assert url == "https://presigned.example.com/key"


# ---------------------------------------------------------------------------
# upload_blob routing
# ---------------------------------------------------------------------------


class TestUploadRouting:
    @patch.dict("os.environ", {"CURIA_STORAGE_BACKEND": "local"})
    async def test_upload_routes_to_local(self):
        with patch.object(blob, "_upload_local", new=AsyncMock(return_value="file:///x")) as mock_local:
            url = await blob.upload_blob(b"data", "k")
        assert url == "file:///x"
        mock_local.assert_awaited_once_with(b"data", "k")

    @patch.dict("os.environ", {"CURIA_STORAGE_BACKEND": "s3"})
    async def test_upload_routes_to_s3(self):
        with patch.object(blob, "_upload_s3", new=AsyncMock(return_value="s3://b/k")) as mock_s3:
            url = await blob.upload_blob(b"data", "k", "audio/mpeg")
        assert url == "s3://b/k"
        mock_s3.assert_awaited_once_with(b"data", "k", "audio/mpeg")
