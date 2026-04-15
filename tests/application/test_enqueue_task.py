"""Tests for EnqueueTaskUseCase."""

from __future__ import annotations

import aiosqlite

from metis.application.enqueue_task import EnqueueTaskInput, EnqueueTaskUseCase
from metis.domain.value_objects import TaskStatus
from metis.infrastructure.sqlite_task_store import SqliteTaskStore


class TestEnqueueTask:
    async def test_should_create_pending_task(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        use_case = EnqueueTaskUseCase(task_store=store)

        result = await use_case.execute(
            EnqueueTaskInput(type="classify", payload={"text": "hello"})
        )

        assert result.is_ok
        task = await store.get(result.value)
        assert task is not None
        assert task.status == TaskStatus.PENDING
        assert task.type == "classify"
        assert task.payload == {"text": "hello"}

    async def test_should_use_custom_priority_and_ttl(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        use_case = EnqueueTaskUseCase(task_store=store)

        result = await use_case.execute(
            EnqueueTaskInput(type="validate", payload={}, priority=10, ttl_seconds=60)
        )

        assert result.is_ok
        task = await store.get(result.value)
        assert task is not None
        assert task.priority.value == 10
        assert task.ttl_seconds == 60
