"""
tests/test_account_external.py
Tests for the external cleanup paths in core/account.py (lines 119-143):
audio blob deletion, Firebase deletion, Apple token revocation.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.account as account


class FakeConn:
    """Minimal conn stub that returns apple token data."""

    def __init__(self, *, audio_keys, firebase_uid, apple_token=None, apple_client_id=None):
        self._audio_keys = audio_keys
        self._firebase_uid = firebase_uid
        self._apple_token = apple_token
        self._apple_client_id = apple_client_id
        self.executed = []

    async def fetch(self, sql, *args):
        s = " ".join(sql.split())
        if "audio_url FROM episode" in s:
            return [{"audio_url": k} for k in self._audio_keys]
        if "id FROM source WHERE user_id" in s:
            return []
        if "column_name = 'user_id'" in s:
            return [{"table_name": "source"}]
        if "column_name = 'source_id'" in s:
            return []
        return []

    async def fetchval(self, sql, *args):
        s = " ".join(sql.split())
        if "table_name='source_similarity'" in s:
            return None
        if "firebase_uid FROM users" in s:
            return self._firebase_uid
        return None

    async def fetchrow(self, sql, *args):
        return {"apple_refresh_token": self._apple_token, "apple_client_id": self._apple_client_id}

    async def execute(self, sql, *args):
        self.executed.append(" ".join(sql.split()))
        return "DELETE 1"

    def transaction(self):
        conn = self

        class _Txn:
            async def __aenter__(self):
                conn.executed.append("BEGIN")
                return None

            async def __aexit__(self, *a):
                conn.executed.append("COMMIT")
                return False

        return _Txn()


def _patch_db(conn):
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return patch.object(account, "get_db", return_value=ctx)


class TestAppleRevocation:
    async def test_apple_revoke_called(self):
        conn = FakeConn(
            audio_keys=[], firebase_uid=None,
            apple_token="rt-abc", apple_client_id="com.app",
        )
        mock_revoke = AsyncMock()
        with _patch_db(conn), \
             patch("core.storage.blob.delete_blobs", new=AsyncMock()), \
             patch("core.firebase.delete_user", return_value=True), \
             patch("core.apple.revoke", mock_revoke):
            await account.delete_account("u1")
        mock_revoke.assert_awaited_once_with("rt-abc", "com.app")

    async def test_apple_revoke_skipped_when_no_token(self):
        conn = FakeConn(
            audio_keys=[], firebase_uid=None,
            apple_token=None, apple_client_id=None,
        )
        mock_revoke = AsyncMock()
        with _patch_db(conn), \
             patch("core.storage.blob.delete_blobs", new=AsyncMock()), \
             patch("core.firebase.delete_user", return_value=True), \
             patch("core.apple.revoke", mock_revoke):
            await account.delete_account("u2")
        mock_revoke.assert_not_awaited()

    async def test_apple_revoke_failure_does_not_break_delete(self):
        conn = FakeConn(
            audio_keys=[], firebase_uid=None,
            apple_token="rt-abc", apple_client_id="com.app",
        )
        with _patch_db(conn), \
             patch("core.storage.blob.delete_blobs", new=AsyncMock()), \
             patch("core.firebase.delete_user", return_value=True), \
             patch("core.apple.revoke", new=AsyncMock(side_effect=RuntimeError("apple down"))):
            summary = await account.delete_account("u3")  # must not raise
        assert summary["users"] == 1


class TestFirebaseCleanup:
    async def test_firebase_called_when_uid_present(self):
        conn = FakeConn(audio_keys=[], firebase_uid="fb-123")
        fb = MagicMock(return_value=True)
        with _patch_db(conn), \
             patch("core.storage.blob.delete_blobs", new=AsyncMock()), \
             patch("core.firebase.delete_user", new=fb):
            await account.delete_account("u4")
        fb.assert_called_once_with("fb-123")

    async def test_firebase_failure_does_not_break_delete(self):
        conn = FakeConn(audio_keys=[], firebase_uid="fb-123")
        with _patch_db(conn), \
             patch("core.storage.blob.delete_blobs", new=AsyncMock()), \
             patch("core.firebase.delete_user", side_effect=RuntimeError("firebase down")):
            summary = await account.delete_account("u5")
        assert summary["users"] == 1


class TestAudioCleanup:
    async def test_audio_cleanup_called(self):
        conn = FakeConn(audio_keys=["audio/a.mp3", "audio/b.mp3"], firebase_uid=None)
        blobs = AsyncMock()
        with _patch_db(conn), \
             patch("core.storage.blob.delete_blobs", new=blobs), \
             patch("core.firebase.delete_user", return_value=True):
            await account.delete_account("u6")
        blobs.assert_awaited_once()
        assert blobs.await_args.args[0] == ["audio/a.mp3", "audio/b.mp3"]

    async def test_audio_cleanup_skipped_when_empty(self):
        conn = FakeConn(audio_keys=[], firebase_uid=None)
        blobs = AsyncMock()
        with _patch_db(conn), \
             patch("core.storage.blob.delete_blobs", new=blobs), \
             patch("core.firebase.delete_user", return_value=True):
            await account.delete_account("u7")
        blobs.assert_not_awaited()
