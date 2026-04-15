"""SQLite database initialization and connection management.

NOT responsible for:
- Task or heartbeat logic (see domain entities)
- Query construction (see SqliteTaskStore, SqliteHeartbeatStore)
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

import aiosqlite

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    result      TEXT,
    priority    INTEGER NOT NULL DEFAULT 0,
    ttl_seconds INTEGER NOT NULL DEFAULT 300,
    created_at  TEXT NOT NULL,
    claimed_at  TEXT,
    completed_at TEXT,
    capabilities_required TEXT NOT NULL DEFAULT '[]',
    session_id    TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER
);

CREATE TABLE IF NOT EXISTS heartbeats (
    worker_id    TEXT PRIMARY KEY,
    capabilities TEXT NOT NULL,
    last_seen    TEXT NOT NULL
);
"""


_MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN session_id TEXT",
]


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply schema migrations idempotently for existing databases."""
    for sql in _MIGRATIONS:
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute(sql)
    conn.commit()


async def _run_migrations_async(conn: aiosqlite.Connection) -> None:
    """Apply schema migrations idempotently for existing databases."""
    for sql in _MIGRATIONS:
        with contextlib.suppress(Exception):
            await conn.execute(sql)
    await conn.commit()


async def init_async_database(db_path: str) -> aiosqlite.Connection:
    """Create tables and configure WAL mode. Returns an open async connection."""
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(str(path))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA busy_timeout=5000")
    await conn.executescript(SCHEMA_SQL)
    await conn.commit()
    await _run_migrations_async(conn)
    return conn


def init_sync_database(db_path: str) -> sqlite3.Connection:
    """Create tables and configure WAL mode. Returns an open sync connection."""
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    _run_migrations(conn)
    return conn
