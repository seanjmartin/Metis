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
    """Persists tasks in SQLite with atomic claim support and capability filtering.

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
                               ttl_seconds, created_at, claimed_at, completed_at,
                               cancelled_at, capabilities_required, session_id,
                               input_tokens, output_tokens,
                               error_code, error_message,
                               input_prompt, input_schema, input_response, input_seq)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                task.cancelled_at.isoformat() if task.cancelled_at else None,
                json.dumps(task.capabilities_required),
                task.session_id,
                task.input_tokens,
                task.output_tokens,
                task.error_code,
                task.error_message,
                task.input_prompt,
                json.dumps(task.input_schema) if task.input_schema is not None else None,
                json.dumps(task.input_response) if task.input_response is not None else None,
                task.input_seq,
            ),
        )
        await self._conn.commit()

    async def get(self, task_id: TaskId) -> Task | None:
        cursor = await self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id.value,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._to_entity(row)

    async def claim_next(
        self,
        capabilities: list[str],
        worker_id: WorkerId,
        session_id: str | None = None,
    ) -> Task | None:
        """Atomically claim the highest-priority pending task matching capabilities.

        A task is claimable if every entry in its capabilities_required list
        is present in the worker's capabilities list. Tasks with empty
        capabilities_required are claimable by any worker.

        When session_id is provided, only tasks with that session_id are claimable.
        When session_id is None, all tasks are claimable (backward compatible).
        """
        cursor = await self._conn.execute(
            """
            UPDATE tasks
            SET status = 'claimed', claimed_at = ?
            WHERE id = (
                SELECT id FROM tasks
                WHERE status = 'pending'
                  AND (? IS NULL OR session_id = ?)
                  AND NOT EXISTS (
                      SELECT 1 FROM json_each(tasks.capabilities_required) AS req
                      WHERE req.value NOT IN (SELECT value FROM json_each(?))
                  )
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
            )
            RETURNING *
            """,
            (datetime.now(UTC).isoformat(), session_id, session_id, json.dumps(capabilities)),
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
            SET status = ?, result = ?, claimed_at = ?, completed_at = ?,
                cancelled_at = ?,
                input_tokens = ?, output_tokens = ?,
                error_code = ?, error_message = ?,
                input_prompt = ?, input_schema = ?, input_response = ?, input_seq = ?
            WHERE id = ?
            """,
            (
                task.status.value,
                json.dumps(task.result) if task.result is not None else None,
                task.claimed_at.isoformat() if task.claimed_at else None,
                task.completed_at.isoformat() if task.completed_at else None,
                task.cancelled_at.isoformat() if task.cancelled_at else None,
                task.input_tokens,
                task.output_tokens,
                task.error_code,
                task.error_message,
                task.input_prompt,
                json.dumps(task.input_schema) if task.input_schema is not None else None,
                json.dumps(task.input_response) if task.input_response is not None else None,
                task.input_seq,
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
        """Mark non-terminal tasks past their TTL as expired. Returns count."""
        cursor = await self._conn.execute(
            """
            UPDATE tasks
            SET status = 'expired'
            WHERE status IN ('pending', 'claimed', 'input_required')
              AND datetime(created_at, '+' || ttl_seconds || ' seconds') <= datetime(?)
            """,
            (now.isoformat(),),
        )
        await self._conn.commit()
        return cursor.rowcount

    def _to_entity(self, row: aiosqlite.Row) -> Task:
        keys = row.keys()

        def _opt(key: str) -> object | None:
            return row[key] if key in keys else None

        cancelled_at_raw = _opt("cancelled_at")
        input_schema_raw = _opt("input_schema")
        input_response_raw = _opt("input_response")
        input_seq_raw = _opt("input_seq")

        return Task(
            id=TaskId(value=row["id"]),
            type=row["type"],
            payload=json.loads(row["payload"]),
            status=TaskStatus(row["status"]),
            result=json.loads(row["result"]) if row["result"] is not None else None,
            priority=TaskPriority(value=row["priority"]),
            ttl_seconds=row["ttl_seconds"],
            capabilities_required=json.loads(row["capabilities_required"]),
            session_id=row["session_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            claimed_at=(datetime.fromisoformat(row["claimed_at"]) if row["claimed_at"] else None),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
            cancelled_at=(datetime.fromisoformat(cancelled_at_raw) if cancelled_at_raw else None),
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            error_code=_opt("error_code"),
            error_message=_opt("error_message"),
            input_prompt=_opt("input_prompt"),
            input_schema=json.loads(input_schema_raw) if input_schema_raw else None,
            input_response=json.loads(input_response_raw) if input_response_raw else None,
            input_seq=input_seq_raw if input_seq_raw is not None else 0,
        )
