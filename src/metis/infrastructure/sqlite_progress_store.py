"""SQLite-backed implementation of ProgressStore.

Progress updates are append-only. Each append increments the per-task seq
monotonically; consumers poll via tail_since(last_seq) to get new entries.

NOT responsible for:
- Rate limiting (coalesce upstream in the worker tool)
- Broadcasting updates (consumers poll by design — SQLite is the bus)
"""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

from metis.domain.protocols import ProgressUpdate
from metis.domain.value_objects import TaskId


class SqliteProgressStore:
    """Append progress rows and tail them by seq."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def append(
        self,
        task_id: TaskId,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> int:
        cursor = await self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS s FROM task_progress WHERE task_id = ?",
            (task_id.value,),
        )
        row = await cursor.fetchone()
        next_seq = (row["s"] if row is not None else 0) + 1

        await self._conn.execute(
            """
            INSERT INTO task_progress (task_id, seq, progress, total, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                task_id.value,
                next_seq,
                progress,
                total,
                message,
                datetime.now(UTC).isoformat(),
            ),
        )
        await self._conn.commit()
        return next_seq

    async def tail_since(self, task_id: TaskId, last_seq: int) -> list[ProgressUpdate]:
        cursor = await self._conn.execute(
            """
            SELECT seq, progress, total, message, created_at
            FROM task_progress
            WHERE task_id = ? AND seq > ?
            ORDER BY seq ASC
            """,
            (task_id.value, last_seq),
        )
        rows = await cursor.fetchall()
        return [
            ProgressUpdate(
                task_id=task_id,
                seq=row["seq"],
                progress=row["progress"],
                total=row["total"],
                message=row["message"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]
