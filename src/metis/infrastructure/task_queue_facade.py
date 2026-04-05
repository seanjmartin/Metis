"""Public facade for the Metis task queue — the deep module.

This is the primary integration point for MCP servers. It hides the
layered internals behind a 3-method API.

NOT responsible for:
- Executing tasks (see dispatcher agent)
- Exposing MCP tools (see metis.presentation.worker_server)
- Managing dispatcher lifecycle (see self-healing protocol)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from metis.domain.value_objects import TaskId
from metis.infrastructure.database import SCHEMA_SQL

_POLL_INTERVAL_SECONDS = 0.1


class TaskQueue:
    """Enqueue tasks and wait for results via SQLite.

    This is the public API for integrating MCP servers. Usage:

        queue = TaskQueue(db_path="~/.my-server/metis.db")
        task_id = queue.enqueue(Task(type="classify", payload={...}))
        result = await queue.wait_for_result(task_id, timeout=60)

    NOT responsible for:
    - Executing tasks (see dispatcher agent)
    - Exposing MCP tools (see metis.presentation.worker_server)
    - Managing dispatcher lifecycle (see self-healing protocol)
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = str(Path(db_path).expanduser())
        self._sync_conn: sqlite3.Connection | None = None

    def _get_sync_conn(self) -> sqlite3.Connection:
        if self._sync_conn is None:
            path = Path(self._db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._sync_conn = sqlite3.connect(self._db_path)
            self._sync_conn.row_factory = sqlite3.Row
            self._sync_conn.execute("PRAGMA journal_mode=WAL")
            self._sync_conn.execute("PRAGMA busy_timeout=5000")
            self._sync_conn.executescript(SCHEMA_SQL)
            self._sync_conn.commit()
        return self._sync_conn

    def enqueue(
        self,
        *,
        type: str,
        payload: dict,
        priority: int = 0,
        ttl_seconds: int = 300,
    ) -> TaskId:
        """Create and enqueue a new task. Synchronous — safe from any context.

        Returns the TaskId for later retrieval via wait_for_result().
        """
        conn = self._get_sync_conn()
        task_id = TaskId.generate()
        now = datetime.now(UTC)

        conn.execute(
            """
            INSERT INTO tasks (id, type, payload, status, priority,
                               ttl_seconds, created_at)
            VALUES (?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                task_id.value,
                type,
                json.dumps(payload),
                priority,
                ttl_seconds,
                now.isoformat(),
            ),
        )
        conn.commit()

        # Lazily expire stale tasks
        conn.execute(
            """
            UPDATE tasks SET status = 'expired'
            WHERE status IN ('pending', 'claimed')
              AND datetime(created_at, '+' || ttl_seconds || ' seconds') <= datetime(?)
            """,
            (now.isoformat(),),
        )
        conn.commit()

        return task_id

    async def wait_for_result(
        self, task_id: TaskId, timeout: float = 300.0
    ) -> dict[str, Any] | None:
        """Wait for a task's result. Async — polls until result or timeout.

        Returns the result dict, or None if timeout expires.
        Raises MetisError subclass if the task failed (e.g. TaskExpiredError).
        """
        from metis.infrastructure.database import init_async_database

        conn = await init_async_database(self._db_path)
        try:
            from metis.application.wait_for_result import (
                WaitForResultInput,
                WaitForResultUseCase,
            )
            from metis.infrastructure.sqlite_task_store import SqliteTaskStore

            store = SqliteTaskStore(conn)
            use_case = WaitForResultUseCase(task_store=store)

            result = await use_case.execute(
                WaitForResultInput(
                    task_id=task_id.value, timeout_seconds=timeout
                )
            )

            if result.is_error:
                raise ValueError(f"{result.error.code}: {result.error.message}")

            return result.value
        finally:
            await conn.close()

    def is_worker_alive(self, timeout_seconds: int = 60) -> bool:
        """Check if any dispatcher worker has a recent heartbeat. Synchronous."""
        conn = self._get_sync_conn()
        row = conn.execute(
            "SELECT last_seen FROM heartbeats ORDER BY last_seen DESC LIMIT 1"
        ).fetchone()

        if row is None:
            return False

        last_seen = datetime.fromisoformat(row["last_seen"])
        return datetime.now(UTC) < last_seen + timedelta(seconds=timeout_seconds)

    def close(self) -> None:
        """Close the underlying sync connection."""
        if self._sync_conn is not None:
            self._sync_conn.close()
            self._sync_conn = None
