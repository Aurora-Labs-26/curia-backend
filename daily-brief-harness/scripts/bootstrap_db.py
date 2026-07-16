"""
(Re-)applies db/001_schema.sql and db/002_seed_topics.sql against DATABASE_URL.

The postgres Docker image only auto-runs files under docker-entrypoint-initdb.d
the FIRST time its volume is created. This script lets you re-apply schema
changes afterward without blowing away the volume (`docker compose down -v`
is the alternative, heavier reset). Every statement in both files is written
to be idempotent (IF NOT EXISTS / ON CONFLICT DO NOTHING), so re-running this
is always safe.

Usage:
    python -m scripts.bootstrap_db
"""

import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()

DB_DIR = Path(__file__).resolve().parent.parent / "db"
SQL_FILES = [
    "001_schema.sql",
    "002_seed_topics.sql",
    "003_eval_schema.sql",
    "004_eval_llm_ranking_output.sql",
    "005_eval_gold_schema.sql",
    "006_drop_glimpse_columns.sql",
    "007_eval_judge_schema.sql",
    "008_soften_freeze.sql",
    "009_order_correctness_labels.sql",
    "010_drop_significance_rank.sql",
    "011_binary_relevance_label.sql",
    "012_order_ranking_label.sql",
    "013_judge_cost_logging.sql",
    "014_faithfulness_memoization.sql",
    "015_judging_settings.sql",
    "016_faithfulness_regen.sql",
]

DEFAULT_DATABASE_URL = "postgresql://curia:curia_dev@localhost:5433/curia_harness"


async def main() -> None:
    database_url = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    conn = await asyncpg.connect(database_url)
    try:
        for filename in SQL_FILES:
            sql_path = DB_DIR / filename
            print(f"Applying {sql_path} ...")
            sql = sql_path.read_text(encoding="utf-8")
            await conn.execute(sql)
            print(f"  OK ({filename})")
    finally:
        await conn.close()
    print("Bootstrap complete.")


if __name__ == "__main__":
    asyncio.run(main())
