"""
core/account.py
Full account deletion — App Store Guideline 5.1.1(v): an account the user can
delete from inside the app, wiping ALL associated data (not a deactivation).

Design: drift-proof. Most user-owned tables key on `user_id` as plain TEXT (not
a FK, per migration 0002), so deleting the users row does NOT cascade. Rather
than hand-list tables (which rots as the schema changes — and the live DB has
drifted ahead of this branch), we discover every table with a `user_id` column
(and source-child tables by `source_id`) from information_schema at runtime and
wipe them in FK-safe order, inside one transaction.

Order: source-children → user_id tables → source (cascades stragglers) → users.
Then best-effort: S3 audio objects, Firebase auth user (outside the txn — their
failure must not roll back the DB delete; a missing token in either is not data).
"""

from __future__ import annotations

from loguru import logger

from core.db.connection import get_db

# Never DELETE-by-user_id these (handled explicitly or must survive).
_PROTECTED = {"users", "alembic_version"}


async def delete_account(user_id: str) -> dict:
    """Delete the user and all associated data. Returns a per-table delete summary."""
    summary: dict[str, int] = {}

    async with get_db() as conn:
        # ---- gather what we need before deleting -------------------------------
        audio_rows = await conn.fetch(
            "SELECT audio_url FROM episode WHERE user_id = $1 AND audio_url IS NOT NULL",
            user_id,
        )
        audio_keys = [r["audio_url"] for r in audio_rows if r["audio_url"]]

        src_rows = await conn.fetch("SELECT id FROM source WHERE user_id = $1", user_id)
        source_ids = [r["id"] for r in src_rows]

        uid_tables = [
            r["table_name"]
            for r in await conn.fetch(
                """
                SELECT table_name FROM information_schema.columns
                WHERE table_schema = 'public' AND column_name = 'user_id'
                """
            )
            if r["table_name"] not in _PROTECTED
        ]
        sid_tables = [
            r["table_name"]
            for r in await conn.fetch(
                """
                SELECT table_name FROM information_schema.columns
                WHERE table_schema = 'public' AND column_name = 'source_id'
                """
            )
            if r["table_name"] not in _PROTECTED
        ]
        has_similarity = bool(
            await conn.fetchval(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='source_similarity'"
            )
        )

        firebase_uid = await conn.fetchval(
            "SELECT firebase_uid FROM users WHERE id = $1", user_id
        )

        def _count(res: str) -> int:
            # asyncpg execute() returns e.g. "DELETE 7"
            try:
                return int(res.split()[-1])
            except (ValueError, IndexError):
                return 0

        # ---- delete, FK-safe order, one transaction ---------------------------
        async with conn.transaction():
            if source_ids:
                # source_similarity is the oddball: source_a/source_b, no FK/cascade.
                if has_similarity:
                    res = await conn.execute(
                        "DELETE FROM source_similarity "
                        "WHERE source_a = ANY($1::uuid[]) OR source_b = ANY($1::uuid[])",
                        source_ids,
                    )
                    summary["source_similarity"] = _count(res)
                # generic source-child tables keyed on source_id (cascade or not)
                for t in sid_tables:
                    res = await conn.execute(
                        f'DELETE FROM "{t}" WHERE source_id = ANY($1::uuid[])', source_ids
                    )
                    summary[t] = summary.get(t, 0) + _count(res)

            # everything keyed on user_id, except `source` (deleted last so its
            # FK-cascade children are swept even if not covered above)
            for t in uid_tables:
                if t == "source":
                    continue
                res = await conn.execute(f'DELETE FROM "{t}" WHERE user_id = $1', user_id)
                summary[t] = summary.get(t, 0) + _count(res)

            res = await conn.execute("DELETE FROM source WHERE user_id = $1", user_id)
            summary["source"] = summary.get("source", 0) + _count(res)

            res = await conn.execute("DELETE FROM users WHERE id = $1", user_id)
            summary["users"] = _count(res)

    logger.info(f"[account] deleted user_id={user_id} rows={summary}")

    # ---- external systems, best-effort (never roll back the DB delete) --------
    if audio_keys:
        try:
            from core.storage.blob import delete_blobs

            await delete_blobs(audio_keys)
        except Exception as exc:
            logger.warning(f"[account] audio cleanup failed for {user_id}: {exc}")

    if firebase_uid:
        try:
            from core.firebase import delete_user as fb_delete_user

            fb_delete_user(firebase_uid)
        except Exception as exc:
            logger.warning(f"[account] firebase cleanup failed for {user_id}: {exc}")

    return summary
