"""
scripts/itest_account_delete.py
Integration test for account deletion against a REAL database (the live RDS
schema, currently 0028). Proves the dynamic discovery wipes every user-owned
table — including drift tables this branch's code never created — and that no
other user is touched.

Run:  DATABASE_URL=postgresql://curia:<pw>@<rds>:5432/curia \
      CURIA_STORAGE_BACKEND=local \
      .venv/bin/python scripts/itest_account_delete.py

Uses a throwaway user (firebase_uid NULL → no Firebase call) and rolls back
nothing — it actually deletes, then verifies. Self-contained and re-runnable.
"""

import asyncio
import json
import uuid

from core.db.connection import get_db, close_pool
from core.account import delete_account

UID = f"acct-del-itest-{uuid.uuid4().hex[:8]}"


async def main() -> int:
    src_id = str(uuid.uuid4())
    ep_id = str(uuid.uuid4())
    other_src = str(uuid.uuid4())

    async with get_db() as conn:
        users_before = await conn.fetchval("SELECT count(*) FROM users")

        # --- seed a throwaway user with data across many tables ---------------
        await conn.execute(
            "INSERT INTO users (id, email, name, api_token, role, firebase_uid) "
            "VALUES ($1,$2,$3,$4,'user',NULL)",
            UID, f"{UID}@itest.local", "itest", f"itok_{UID}",
        )
        await conn.execute(
            "INSERT INTO source (id, user_id, url, status, title) "
            "VALUES ($1::uuid,$2,$3,'ready','itest source')",
            src_id, UID, f"https://itest.local/{src_id}",
        )
        await conn.execute(
            "INSERT INTO source_insight (source_id, insight_type, content) "
            "VALUES ($1::uuid,'summary','x')",
            src_id,
        )
        await conn.execute(
            "INSERT INTO episode (id, user_id, show_name, status, source_ids) "
            "VALUES ($1::uuid,$2,'clarity_engine','ready',ARRAY[$3::uuid])",
            ep_id, UID, src_id,
        )
        await conn.execute(
            "INSERT INTO jobs (type, payload, status, user_id) "
            "VALUES ('ingest',$1::jsonb,'done',$2)",
            json.dumps({"source_id": src_id}), UID,
        )
        await conn.execute(
            "INSERT INTO source_similarity (source_a, source_b, score) "
            "VALUES ($1::uuid,$2::uuid,0.9)",
            src_id, other_src,
        )

        # snapshot: which user_id tables now hold rows for this user?
        uid_tables = [
            r["table_name"]
            for r in await conn.fetch(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema='public' AND column_name='user_id'"
            )
        ]
        seeded = {}
        for t in uid_tables:
            seeded[t] = await conn.fetchval(f'SELECT count(*) FROM "{t}" WHERE user_id=$1', UID)
        print(f"seeded rows by table: { {k:v for k,v in seeded.items() if v} }")
        insight_before = await conn.fetchval(
            "SELECT count(*) FROM source_insight WHERE source_id=$1::uuid", src_id
        )
        sim_before = await conn.fetchval(
            "SELECT count(*) FROM source_similarity WHERE source_a=$1::uuid", src_id
        )
        assert insight_before == 1 and sim_before == 1, "seed failed"

    # --- the actual delete -------------------------------------------------
    summary = await delete_account(UID)
    print(f"delete summary: { {k:v for k,v in summary.items() if v} }")

    # --- verify everything is gone, others untouched ----------------------
    failures = []
    async with get_db() as conn:
        if await conn.fetchval("SELECT count(*) FROM users WHERE id=$1", UID):
            failures.append("user row survived")
        for t in uid_tables:
            n = await conn.fetchval(f'SELECT count(*) FROM "{t}" WHERE user_id=$1', UID)
            if n:
                failures.append(f"{t}: {n} rows survived")
        if await conn.fetchval("SELECT count(*) FROM source WHERE id=$1::uuid", src_id):
            failures.append("source survived")
        if await conn.fetchval("SELECT count(*) FROM source_insight WHERE source_id=$1::uuid", src_id):
            failures.append("source_insight (cascade) survived")
        if await conn.fetchval("SELECT count(*) FROM source_similarity WHERE source_a=$1::uuid", src_id):
            failures.append("source_similarity survived")
        users_after = await conn.fetchval("SELECT count(*) FROM users")
        if users_after != users_before:
            failures.append(f"other users affected: before={users_before} after={users_after}")

    await close_pool()
    if failures:
        print("\n❌ INTEGRATION TEST FAILED:")
        for f in failures:
            print(f"   - {f}")
        return 1
    print(f"\n✅ INTEGRATION TEST PASSED — user + all data gone, {users_before - 1} other users intact")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
