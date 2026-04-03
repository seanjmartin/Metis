"""Tests for WaitForResultUseCase."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import aiosqlite

from metis.application.deliver_result import DeliverResultInput, DeliverResultUseCase
from metis.application.enqueue_task import EnqueueTaskInput, EnqueueTaskUseCase
from metis.application.poll_task import PollTaskInput, PollTaskUseCase
from metis.application.wait_for_result import WaitForResultInput, WaitForResultUseCase
from metis.domain.entities import Task
from metis.domain.value_objects import TaskId, TaskPriority
from metis.infrastructure.sqlite_heartbeat_store import SqliteHeartbeatStore
from metis.infrastructure.sqlite_task_store import SqliteTaskStore


class TestWaitForResult:
    async def test_should_return_none_on_timeout(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        task_store = SqliteTaskStore(db_conn)
        enqueue = EnqueueTaskUseCase(task_store=task_store)
        wait = WaitForResultUseCase(task_store=task_store)

        enqueue_result = await enqueue.execute(
            EnqueueTaskInput(type="test", payload={})
        )

        result = await wait.execute(
            WaitForResultInput(
                task_id=enqueue_result.value.value,
                timeout_seconds=0.3,
            )
        )

        assert result.is_ok
        assert result.value is None

    async def test_should_return_result_when_delivered(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        task_store = SqliteTaskStore(db_conn)
        hb_store = SqliteHeartbeatStore(db_conn)
        enqueue = EnqueueTaskUseCase(task_store=task_store)
        poll = PollTaskUseCase(task_store=task_store, heartbeat_store=hb_store)
        deliver = DeliverResultUseCase(task_store=task_store)
        wait = WaitForResultUseCase(task_store=task_store)

        enqueue_result = await enqueue.execute(
            EnqueueTaskInput(type="test", payload={})
        )
        task_id_str = enqueue_result.value.value

        async def simulate_worker() -> None:
            await asyncio.sleep(0.2)
            await poll.execute(PollTaskInput(worker_id="w1", capabilities=[]))
            await deliver.execute(
                DeliverResultInput(task_id=task_id_str, result={"answer": 42})
            )

        worker_task = asyncio.create_task(simulate_worker())

        result = await wait.execute(
            WaitForResultInput(task_id=task_id_str, timeout_seconds=5.0)
        )

        await worker_task
        assert result.is_ok
        assert result.value == {"answer": 42}

    async def test_should_detect_expired_task(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        task_store = SqliteTaskStore(db_conn)

        task = Task(
            id=TaskId.generate(),
            type="test",
            payload={},
            priority=TaskPriority(),
            ttl_seconds=0,
            created_at=datetime.now(UTC) - timedelta(seconds=10),
        )
        await task_store.insert(task)

        task.expire()
        await task_store.update(task)

        wait = WaitForResultUseCase(task_store=task_store)
        result = await wait.execute(
            WaitForResultInput(task_id=task.id.value, timeout_seconds=1.0)
        )

        assert result.is_error
        assert result.error.code == "TASK_EXPIRED"

    async def test_should_fail_for_missing_task(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        task_store = SqliteTaskStore(db_conn)
        wait = WaitForResultUseCase(task_store=task_store)

        result = await wait.execute(
            WaitForResultInput(
                task_id="00000000-0000-0000-0000-000000000000",
                timeout_seconds=1.0,
            )
        )

        assert result.is_error
        assert result.error.code == "TASK_NOT_FOUND"
