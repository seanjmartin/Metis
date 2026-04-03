"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest_asyncio

from metis.infrastructure.database import init_async_database


@pytest_asyncio.fixture
async def db_conn(tmp_path: Path) -> aiosqlite.Connection:
    """Provide an initialized async SQLite connection for tests."""
    db_path = str(tmp_path / "test_metis.db")
    conn = await init_async_database(db_path)
    yield conn
    await conn.close()
