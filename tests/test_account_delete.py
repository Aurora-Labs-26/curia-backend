"""
tests/test_account_delete.py
Account deletion (App Store 5.1.1(v)) — verifies the drift-proof delete wipes
every user-owned table, sweeps source children + the source_similarity oddball,
cleans S3 + Firebase, and isolates external-system failures from the DB delete.
boto3/firebase/DB are all mocked; no infra required.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.account as account


class FakeConn:
    """Minimal asyncpg-connection stub recording every execute() SQL."""

    def __init__(self, *, user_tables, source_tables, has_similarity,
                 source_ids, audio_keys, firebase_uid):
        self._user_tables = user_tables
        self._source_tables = source_tables
        self._has_similarity = has_similarity
        self._source_ids = source_ids
        self._audio_keys = audio_keys
        self._firebase_uid = firebase_uid
        self.executed: list[str] = []

    async def fetch(self, sql, *args):
        s = " ".join(sql.split())
        if "audio_url FROM episode" in s:
            return [{"audio_url": k} for k in self._audio_keys]
        if "id FROM source WHERE user_id" in s:
            return [{"id": sid} for sid in self._source_ids]
        if "column_name = 'user_id'" in s:
            return [{"table_name": t} for t in self._user_tables]
        if "column_name = 'source_id'" in s:
            return [{"table_name": t} for t in self._source_tables]
        return []

    async def fetchval(self, sql, *args):
        s = " ".join(sql.split())
        if "table_name='source_similarity'" in s:
            return 1 if self._has_similarity else None
        if "firebase_uid FROM users" in s:
            return self._firebase_uid
        return None

    async def fetchrow(self, sql, *args):
        # apple token lookup during delete — no Apple link in these fixtures
        return {"apple_refresh_token": None, "apple_client_id": None}

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


def _delete_targets(conn) -> list[str]:
    return [s for s in conn.executed if s.startswith("DELETE FROM")]


class TestDeleteCoverage:
    async def test_wipes_all_user_tables_and_user_last(self):
        conn = FakeConn(
            user_tables=["source", "episode", "jobs", "show_idea", "notification_log"],
            source_tables=["source_insight", "source_embedding"],
            has_similarity=True,
            source_ids=["11111111-1111-1111-1111-111111111111"],
            audio_keys=["audio/ep1.mp3"],
            firebase_uid="fb-uid-1",
        )
        with _patch_db(conn), \
             patch("core.storage.blob.delete_blobs", new=AsyncMock()), \
             patch("core.firebase.delete_user", return_value=True):
            summary = await account.delete_account("u1")

        targets = _delete_targets(conn)
        # every user_id table hit
        for t in ("episode", "jobs", "show_idea", "notification_log", "source"):
            assert any(f'"{t}" WHERE user_id' in s or f"{t} WHERE user_id" in s for s in targets), t
        # users deleted, and it's the LAST delete
        assert targets[-1].startswith("DELETE FROM users")
        # source deleted AFTER the other user tables (cascade safety)
        src_i = next(i for i, s in enumerate(targets) if "FROM source WHERE user_id" in s)
        ep_i = next(i for i, s in enumerate(targets) if "episode" in s)
        assert ep_i < src_i < len(targets) - 1
        assert summary["users"] == 1

    async def test_source_children_and_similarity_swept(self):
        conn = FakeConn(
            user_tables=["source", "episode"],
            source_tables=["source_insight", "source_embedding", "source_primitive_embedding"],
            has_similarity=True,
            source_ids=["22222222-2222-2222-2222-222222222222"],
            audio_keys=[],
            firebase_uid=None,
        )
        with _patch_db(conn), \
             patch("core.storage.blob.delete_blobs", new=AsyncMock()), \
             patch("core.firebase.delete_user", return_value=True):
            await account.delete_account("u2")
        targets = _delete_targets(conn)
        assert any("source_similarity" in s and "source_a" in s for s in targets)
        for t in ("source_insight", "source_embedding", "source_primitive_embedding"):
            assert any(t in s and "source_id = ANY" in s for s in targets), t

    async def test_no_sources_skips_source_child_deletes(self):
        conn = FakeConn(
            user_tables=["source", "episode"],
            source_tables=["source_insight"],
            has_similarity=True,
            source_ids=[],
            audio_keys=[],
            firebase_uid=None,
        )
        with _patch_db(conn), \
             patch("core.storage.blob.delete_blobs", new=AsyncMock()), \
             patch("core.firebase.delete_user", return_value=True):
            await account.delete_account("u3")
        targets = _delete_targets(conn)
        assert not any("source_insight" in s for s in targets)  # no source ids → skip
        assert any("FROM source WHERE user_id" in s for s in targets)  # still clears source table
        assert targets[-1].startswith("DELETE FROM users")

    async def test_protected_tables_never_user_deleted(self):
        conn = FakeConn(
            user_tables=["source", "episode", "users"],  # users wrongly present in discovery
            source_tables=[],
            has_similarity=False,
            source_ids=[],
            audio_keys=[],
            firebase_uid=None,
        )
        # _PROTECTED filtering happens on discovered lists; assert users only
        # deleted once, explicitly, and last.
        with _patch_db(conn), \
             patch("core.storage.blob.delete_blobs", new=AsyncMock()), \
             patch("core.firebase.delete_user", return_value=True):
            await account.delete_account("u4")
        user_deletes = [s for s in _delete_targets(conn) if s.startswith("DELETE FROM users")]
        assert len(user_deletes) == 1


class TestExternalCleanup:
    async def test_audio_and_firebase_called(self):
        conn = FakeConn(
            user_tables=["source", "episode"], source_tables=[], has_similarity=False,
            source_ids=[], audio_keys=["audio/a.mp3", "audio/b.mp3"], firebase_uid="fb-9",
        )
        blobs = AsyncMock()
        fb = MagicMock(return_value=True)
        with _patch_db(conn), \
             patch("core.storage.blob.delete_blobs", new=blobs), \
             patch("core.firebase.delete_user", new=fb):
            await account.delete_account("u5")
        blobs.assert_awaited_once()
        assert blobs.await_args.args[0] == ["audio/a.mp3", "audio/b.mp3"]
        fb.assert_called_once_with("fb-9")

    async def test_s3_failure_does_not_break_delete(self):
        conn = FakeConn(
            user_tables=["source", "episode"], source_tables=[], has_similarity=False,
            source_ids=[], audio_keys=["audio/a.mp3"], firebase_uid="fb-1",
        )
        with _patch_db(conn), \
             patch("core.storage.blob.delete_blobs", new=AsyncMock(side_effect=RuntimeError("s3 down"))), \
             patch("core.firebase.delete_user", return_value=True) as fb:
            summary = await account.delete_account("u6")  # must not raise
        assert summary["users"] == 1
        fb.assert_called_once()  # firebase still attempted after S3 failure

    async def test_no_firebase_uid_skips_firebase(self):
        conn = FakeConn(
            user_tables=["source"], source_tables=[], has_similarity=False,
            source_ids=[], audio_keys=[], firebase_uid=None,
        )
        fb = MagicMock(return_value=True)
        with _patch_db(conn), \
             patch("core.storage.blob.delete_blobs", new=AsyncMock()), \
             patch("core.firebase.delete_user", new=fb):
            await account.delete_account("u7")
        fb.assert_not_called()
