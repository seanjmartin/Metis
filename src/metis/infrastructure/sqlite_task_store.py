"""SQLite-backed implementation of TaskStore protocol.

NOT responsible for:
- Task lifecycle logic (see Task entity)
- Connection management (see database.py)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import aiosqlite

from metis.domain.entities import Task
from metis.domain.value_objects import TaskId, TaskPriority, TaskStatus, WorkerId


class SqliteTaskStore:
    """Persists tasks in SQLite with atomic claim support.

    NOT responsible for:
    - Task status transition validation (see Task entity)
    - Use case orchestration (see application layer)
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def insert(self, task: Task) -> None:
        await self._conn.execute(
            """
            INSERT INTO tasks (id, type, payload, status, result, priority,
                               ttl_seconds, created_at, claimed_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.id.value,
                task.type,
                json.dumps(task.payload),
                task.status.value,
                json.dumps(task.result) if task.result is not None else None,
                task.priority.value,
                task.ttl_seconds,
                task.created_at.isoformat(),
                task.claimed_at.isoformat() if task.claimed_at else None,
                task.completed_at.isoformat() if task.completed_at else None,
            ),
        )
        await self._conn.commit()

    async def get(self, task_id: TaskId) -> Task | None:
        cursor = await self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id.value,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._to_entity(row)

    async def claim_next(
        self, capabilities: list[str], worker_id: WorkerId
    ) -> Task | None:
        """Atomically claim the highest-priority pending task."""
        cursor = await self._conn.execute(
            """
            UPDATE tasks
            SET status = 'claimed', claimed_at = ?
            WHERE id = (
                SELECT id FROM tasks
                WHERE status = 'pending'
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
            )
            RETURNING *
            """,
            (datetime.now(UTC).isoformat(),),
        )
        row = await cursor.fetchone()
        await self._conn.commit()
        if row is None:
            return None
        return self._to_entity(row)

    async def update(self, task: Task) -> None:
        await self._conn.execute(
            """
            UPDATE tasks
            SET status = ?, result = ?, claimed_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                task.status.value,
                json.dumps(task.result) if task.result is not None else None,
                task.claimed_at.isoformat() if task.claimed_at else None,
                task.completed_at.isoformat() if task.completed_at else None,
                task.id.value,
            ),
        )
        await self._conn.commit()

    async def mark_consumed(self, task_id: TaskId) -> None:
        await self._conn.execute(
            "UPDATE tasks SET status = 'consumed' WHERE id = ?",
            (task_id.value,),
        )
        await self._conn.commit()

    async def expire_stale(self, now: datetime) -> int:
        """Mark pending/claimed tasks past their TTL as expired. Returns count."""
        cursor = await self._conn.execute(
            """
            UPDATE tasks
            SET status = 'expired'
            WHERE status IN ('pending', 'claimed')
              AND datetime(created_at, '+' || ttl_seconds || ' seconds') <= datetime(?)
            """,
            (now.isoformat(),),
        )
        await self._conn.commit()
        return cursor.rowcount

    def _to_entity(self, row: aiosqlite.Row) -> Task:
        return Task(
            id=TaskId(value=row["id"]),
            type=row["type"],
            payload=json.loads(row["payload"]),
            status=TaskStatus(row["status"]),
            result=json.loads(row["result"]) if row["result"] is not None else None,
            priority=TaskPriority(value=row["priority"]),
            ttl_seconds=row["ttl_seconds"],
            created_at=datetime.fromisoformat(row["created_at"]),
            claimed_at=datetime.fromisoformat(row["claimed_at"]) if row["claimed_at"] else None,
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
        )
