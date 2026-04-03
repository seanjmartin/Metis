"""Tests for PollTaskUseCase and DeliverResultUseCase."""

from __future__ import annotations

import aiosqlite

from metis.application.deliver_result import DeliverResultInput, DeliverResultUseCase
from metis.application.enqueue_task import EnqueueTaskInput, EnqueueTaskUseCase
from metis.application.poll_task import PollTaskInput, PollTaskUseCase
from metis.domain.value_objects import TaskStatus, WorkerId
from metis.infrastructure.sqlite_heartbeat_store import SqliteHeartbeatStore
from metis.infrastructure.sqlite_task_store import SqliteTaskStore


class TestPollTask:
    async def test_should_claim_pending_task(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        task_store = SqliteTaskStore(db_conn)
        hb_store = SqliteHeartbeatStore(db_conn)
        enqueue = EnqueueTaskUseCase(task_store=task_store)
        poll = PollTaskUseCase(task_store=task_store, heartbeat_store=hb_store)

        enqueue_result = await enqueue.execute(
            EnqueueTaskInput(type="test", payload={"x": 1})
        )

        poll_result = await poll.execute(
            PollTaskInput(worker_id="w1", capabilities=[])
        )

        assert poll_result.is_ok
        task = poll_result.value
        assert task is not None
        assert task.id == enqueue_result.value
        assert task.status == TaskStatus.CLAIMED

    async def test_should_return_none_when_no_tasks(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        task_store = SqliteTaskStore(db_conn)
        hb_store = SqliteHeartbeatStore(db_conn)
        poll = PollTaskUseCase(task_store=task_store, heartbeat_store=hb_store)

        result = await poll.execute(
            PollTaskInput(worker_id="w1", capabilities=[])
        )

        assert result.is_ok
        assert result.value is None

    async def test_should_update_heartbeat(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        task_store = SqliteTaskStore(db_conn)
        hb_store = SqliteHeartbeatStore(db_conn)
        poll = PollTaskUseCase(task_store=task_store, heartbeat_store=hb_store)

        await poll.execute(PollTaskInput(worker_id="w1", capabilities=["browse-as-me"]))

        hb = await hb_store.get(WorkerId(value="w1"))
        assert hb is not None
        assert hb.capabilities == ["browse-as-me"]


class TestDeliverResult:
    async def test_should_complete_claimed_task(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        task_store = SqliteTaskStore(db_conn)
        hb_store = SqliteHeartbeatStore(db_conn)
        enqueue = EnqueueTaskUseCase(task_store=task_store)
        poll = PollTaskUseCase(task_store=task_store, heartbeat_store=hb_store)
        deliver = DeliverResultUseCase(task_store=task_store)

        enqueue_result = await enqueue.execute(
            EnqueueTaskInput(type="test", payload={})
        )
        await poll.execute(PollTaskInput(worker_id="w1", capabilities=[]))

        result = await deliver.execute(
            DeliverResultInput(
                task_id=enqueue_result.value.value,
                result={"answer": 42},
            )
        )

        assert result.is_ok
        task = await task_store.get(enqueue_result.value)
        assert task is not None
        assert task.status == TaskStatus.COMPLETE
        assert task.result == {"answer": 42}

    async def test_should_fail_for_missing_task(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        task_store = SqliteTaskStore(db_conn)
        deliver = DeliverResultUseCase(task_store=task_store)

        result = await deliver.execute(
            DeliverResultInput(
                task_id="00000000-0000-0000-0000-000000000000",
                result={"x": 1},
            )
        )

        assert result.is_error
