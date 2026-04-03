"""SQLite-backed implementation of HeartbeatStore protocol.

NOT responsible for:
- Health decision logic (see CheckHealthUseCase)
- Connection management (see database.py)
"""

from __future__ import annotations

import json
from datetime import datetime

import aiosqlite

from metis.domain.entities import Heartbeat
from metis.domain.value_objects import WorkerId


class SqliteHeartbeatStore:
    """Persists dispatcher heartbeats in SQLite.

    NOT responsible for:
    - Determining if a worker is alive (see Heartbeat.is_alive)
    - Worker lifecycle management (see dispatcher agent)
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def upsert(self, heartbeat: Heartbeat) -> None:
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO heartbeats (worker_id, capabilities, last_seen)
            VALUES (?, ?, ?)
            """,
            (
                heartbeat.worker_id.value,
                json.dumps(heartbeat.capabilities),
                heartbeat.last_seen.isoformat(),
            ),
        )
        await self._conn.commit()

    async def get(self, worker_id: WorkerId) -> Heartbeat | None:
        cursor = await self._conn.execute(
            "SELECT * FROM heartbeats WHERE worker_id = ?",
            (worker_id.value,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._to_entity(row)

    async def get_latest(self) -> Heartbeat | None:
        cursor = await self._conn.execute(
            "SELECT * FROM heartbeats ORDER BY last_seen DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._to_entity(row)

    async def remove(self, worker_id: WorkerId) -> None:
        await self._conn.execute(
            "DELETE FROM heartbeats WHERE worker_id = ?",
            (worker_id.value,),
        )
        await self._conn.commit()

    def _to_entity(self, row: aiosqlite.Row) -> Heartbeat:
        return Heartbeat(
            worker_id=WorkerId(value=row[0]),
            capabilities=json.loads(row[1]),
            last_seen=datetime.fromisoformat(row[2]),
        )
