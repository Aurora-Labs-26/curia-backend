"""
core/db/connection.py
SurrealDB connection and base query functions.
"""

import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from loguru import logger
from surrealdb import AsyncSurreal

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

SURREAL_URL = os.getenv("SURREAL_URL", "ws://localhost:8000/rpc")
SURREAL_USER = os.getenv("SURREAL_USER", "root")
SURREAL_PASSWORD = os.getenv("SURREAL_PASSWORD", "root")
SURREAL_NAMESPACE = os.getenv("SURREAL_NAMESPACE", "curia")
SURREAL_DATABASE = os.getenv("SURREAL_DATABASE", "studio")


@asynccontextmanager
async def get_db():
    db = AsyncSurreal(SURREAL_URL)
    try:
        await db.connect()
        await db.signin({"username": SURREAL_USER, "password": SURREAL_PASSWORD})
        await db.use(SURREAL_NAMESPACE, SURREAL_DATABASE)
        yield db
    finally:
        await db.close()


async def db_query(query: str, params: dict = {}) -> Any:
    async with get_db() as db:
        result = await db.query(query, params)
        return result


async def db_create(table: str, data: dict) -> dict:
    async with get_db() as db:
        result = await db.create(table, data)
        return result


async def db_select(table: str, record_id: str = None) -> Any:
    async with get_db() as db:
        if record_id:
            return await db.select(f"{table}:{record_id}")
        return await db.select(table)


async def db_update(record: str, data: dict) -> dict:
    async with get_db() as db:
        return await db.update(record, data)


async def db_delete(record: str) -> Any:
    async with get_db() as db:
        return await db.delete(record)


async def db_upsert(table: str, record_id: str, data: dict) -> dict:
    async with get_db() as db:
        return await db.upsert(f"{table}:{record_id}", data)
