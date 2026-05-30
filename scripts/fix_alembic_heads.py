"""
One-time fix: remove stale 0024_episode_audio_url from alembic_version.
The DB has both 0024 and 0025_source_thumbnail_url as rows (two heads).
Since 0025 supersedes 0024, delete 0024 so alembic sees a single head.
Idempotent — safe to run on every deploy.
"""
import asyncio
import os
import asyncpg


async def main() -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        deleted = await conn.execute(
            "DELETE FROM alembic_version WHERE version_num = '0024_episode_audio_url'"
        )
        if deleted != "DELETE 0":
            print(f"[fix_alembic_heads] removed stale head: 0024_episode_audio_url ({deleted})")
        else:
            print("[fix_alembic_heads] nothing to clean")
    finally:
        await conn.close()


asyncio.run(main())
