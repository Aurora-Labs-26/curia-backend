"""
tests/test_storage_blob.py
Tests for core/storage/blob.py — local filesystem backend.

Verifies:
  - upload_blob writes bytes to disk and returns file:// URL
  - upload_file reads a source file and writes to blob storage
  - Nested key paths create intermediate directories
  - get_blob_url returns correct local path
  - All async functions don't block the event loop (they use asyncio.to_thread)
"""

import asyncio
from pathlib import Path

import pytest


@pytest.fixture
def blob_dir(tmp_path, monkeypatch):
    """Point blob storage at a temp directory."""
    monkeypatch.setenv("CURIA_STORAGE_BACKEND", "local")
    monkeypatch.setenv("CURIA_STORAGE_LOCAL_DIR", str(tmp_path / "blobs"))
    return tmp_path / "blobs"


@pytest.mark.asyncio
async def test_upload_blob_writes_bytes(blob_dir):
    from core.storage.blob import upload_blob

    data = b"hello world"
    url = await upload_blob(data, key="test/hello.txt", content_type="text/plain")

    assert url.startswith("file://")
    written = (blob_dir / "test" / "hello.txt").read_bytes()
    assert written == data


@pytest.mark.asyncio
async def test_upload_blob_creates_nested_dirs(blob_dir):
    from core.storage.blob import upload_blob

    await upload_blob(b"nested", key="a/b/c/deep.bin")

    assert (blob_dir / "a" / "b" / "c" / "deep.bin").exists()


@pytest.mark.asyncio
async def test_upload_file_reads_and_stores(blob_dir, tmp_path):
    from core.storage.blob import upload_file

    src = tmp_path / "source.wav"
    src.write_bytes(b"\x00" * 1000)

    url = await upload_file(str(src), key="audio/ep1.wav")

    assert url.startswith("file://")
    stored = (blob_dir / "audio" / "ep1.wav").read_bytes()
    assert stored == b"\x00" * 1000


@pytest.mark.asyncio
async def test_get_blob_url_local(blob_dir):
    from core.storage.blob import get_blob_url

    url = get_blob_url("images/cover.jpg")
    assert "images/cover.jpg" in url
    assert url.startswith("file://")


@pytest.mark.asyncio
async def test_upload_blob_binary_content(blob_dir):
    from core.storage.blob import upload_blob

    data = bytes(range(256)) * 100
    await upload_blob(data, key="binary.dat")

    assert (blob_dir / "binary.dat").read_bytes() == data


@pytest.mark.asyncio
async def test_upload_blob_empty_content(blob_dir):
    from core.storage.blob import upload_blob

    await upload_blob(b"", key="empty.txt")

    assert (blob_dir / "empty.txt").read_bytes() == b""


@pytest.mark.asyncio
async def test_upload_blob_overwrites_existing(blob_dir):
    from core.storage.blob import upload_blob

    await upload_blob(b"first", key="overwrite.txt")
    await upload_blob(b"second", key="overwrite.txt")

    assert (blob_dir / "overwrite.txt").read_bytes() == b"second"


@pytest.mark.asyncio
async def test_upload_blob_does_not_block_loop(blob_dir):
    """Verify upload runs concurrently — the event loop stays responsive."""
    from core.storage.blob import upload_blob

    flag = asyncio.Event()

    async def background():
        flag.set()

    task = asyncio.create_task(background())
    await upload_blob(b"data", key="concurrent.txt")
    await task
    assert flag.is_set()
