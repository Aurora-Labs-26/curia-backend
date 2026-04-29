"""
core/db/migrations.py
Sets up all required SurrealDB tables for curia studio.
Run once before first use.
"""

import asyncio
from loguru import logger
from .connection import get_db

SCHEMA = """
-- Sources table
DEFINE TABLE source SCHEMALESS;

-- Source embeddings (chunks + vectors)
DEFINE TABLE source_embedding SCHEMALESS;
DEFINE INDEX embedding_index ON source_embedding FIELDS embedding HNSW DIMENSION 768 DIST COSINE;

-- Source primitive embeddings (core_tensions + counterpoints only — used for clustering)
DEFINE TABLE source_primitive_embedding SCHEMALESS;
DEFINE INDEX primitive_embedding_index ON source_primitive_embedding FIELDS embedding HNSW DIMENSION 768 DIST COSINE;

-- Source insights (LLM-generated per source)
DEFINE TABLE source_insight SCHEMALESS;

-- Episodes
DEFINE TABLE episode SCHEMALESS;

-- Covered topics log
DEFINE TABLE covered_topic SCHEMALESS;

-- Show ideas (from show idea generator)
DEFINE TABLE show_idea SCHEMALESS;

-- Host character memory
DEFINE TABLE host_memory SCHEMALESS;
DEFINE INDEX memory_lookup ON host_memory FIELDS user_id, host UNIQUE;
"""


async def run_migrations():
    logger.info("Running migrations...")
    async with get_db() as db:
        await db.query(SCHEMA)
    logger.info("Migrations complete.")


if __name__ == "__main__":
    asyncio.run(run_migrations())
