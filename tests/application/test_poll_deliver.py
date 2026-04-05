"""Tests for PollTaskUseCase and DeliverResultUseCase."""

from __future__ import annotations

import asyncio

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


class TestLongPoll:
    async def test_should_return_immediately_when_task_available(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        task_store = SqliteTaskStore(db_conn)
        hb_store = SqliteHeartbeatStore(db_conn)
        enqueue = EnqueueTaskUseCase(task_store=task_store)
        poll = PollTaskUseCase(task_store=task_store, heartbeat_store=hb_store)

        await enqueue.execute(EnqueueTaskInput(type="test", payload={}))

        result = await poll.execute(
            PollTaskInput(worker_id="w1", capabilities=[], timeout_seconds=10)
        )

        assert result.is_ok
        assert result.value is not None
        assert result.value.type == "test"

    async def test_should_return_none_after_timeout(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        task_store = SqliteTaskStore(db_conn)
        hb_store = SqliteHeartbeatStore(db_conn)
        poll = PollTaskUseCase(task_store=task_store, heartbeat_store=hb_store)

        result = await poll.execute(
            PollTaskInput(worker_id="w1", capabilities=[], timeout_seconds=1.5)
        )

        assert result.is_ok
        assert result.value is None

    async def test_should_pick_up_task_enqueued_during_wait(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        task_store = SqliteTaskStore(db_conn)
        hb_store = SqliteHeartbeatStore(db_conn)
        enqueue = EnqueueTaskUseCase(task_store=task_store)
        poll = PollTaskUseCase(task_store=task_store, heartbeat_store=hb_store)

        async def enqueue_after_delay() -> None:
            await asyncio.sleep(0.5)
            await enqueue.execute(EnqueueTaskInput(type="delayed", payload={}))

        asyncio.create_task(enqueue_after_delay())

        result = await poll.execute(
            PollTaskInput(worker_id="w1", capabilities=[], timeout_seconds=5)
        )

        assert result.is_ok
        assert result.value is not None
        assert result.value.type == "delayed"

    async def test_should_update_heartbeat_during_long_poll(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        """Heartbeat should be updated at start and end of a long poll."""
        task_store = SqliteTaskStore(db_conn)
        hb_store = SqliteHeartbeatStore(db_conn)
        poll = PollTaskUseCase(task_store=task_store, heartbeat_store=hb_store)

        await poll.execute(
            PollTaskInput(worker_id="w1", capabilities=["test"], timeout_seconds=1.5)
        )

        hb = await hb_store.get(WorkerId(value="w1"))
        assert hb is not None
        assert hb.is_alive(timeout_seconds=10)


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
        assert result.error.code == "TASK_NOT_FOUND"

    async def test_should_fail_for_pending_task(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        """Delivering to a task that hasn't been claimed should fail."""
        task_store = SqliteTaskStore(db_conn)
        enqueue = EnqueueTaskUseCase(task_store=task_store)
        deliver = DeliverResultUseCase(task_store=task_store)

        enqueue_result = await enqueue.execute(
            EnqueueTaskInput(type="test", payload={})
        )

        result = await deliver.execute(
            DeliverResultInput(
                task_id=enqueue_result.value.value,
                result={"x": 1},
            )
        )

        assert result.is_error
        assert result.error.code == "INVALID_TRANSITION"

    async def test_should_fail_for_already_complete_task(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        """Delivering to an already-completed task should fail."""
        task_store = SqliteTaskStore(db_conn)
        hb_store = SqliteHeartbeatStore(db_conn)
        enqueue = EnqueueTaskUseCase(task_store=task_store)
        poll = PollTaskUseCase(task_store=task_store, heartbeat_store=hb_store)
        deliver = DeliverResultUseCase(task_store=task_store)

        enqueue_result = await enqueue.execute(
            EnqueueTaskInput(type="test", payload={})
        )
        await poll.execute(PollTaskInput(worker_id="w1", capabilities=[]))

        # First deliver succeeds
        await deliver.execute(
            DeliverResultInput(
                task_id=enqueue_result.value.value,
                result={"first": True},
            )
        )

        # Second deliver should fail
        result = await deliver.execute(
            DeliverResultInput(
                task_id=enqueue_result.value.value,
                result={"second": True},
            )
        )

        assert result.is_error
        assert result.error.code == "INVALID_TRANSITION"

    async def test_should_store_token_counts(
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
                input_tokens=1500,
                output_tokens=500,
            )
        )

        assert result.is_ok
        task = await task_store.get(enqueue_result.value)
        assert task is not None
        assert task.input_tokens == 1500
        assert task.output_tokens == 500


class TestCapabilityFilteredPolling:
    async def test_should_only_claim_matching_tasks(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        task_store = SqliteTaskStore(db_conn)
        hb_store = SqliteHeartbeatStore(db_conn)
        enqueue = EnqueueTaskUseCase(task_store=task_store)
        poll = PollTaskUseCase(task_store=task_store, heartbeat_store=hb_store)

        # Enqueue a task requiring browse-as-me
        await enqueue.execute(
            EnqueueTaskInput(
                type="browser",
                payload={},
                capabilities_required=["browse-as-me"],
            )
        )

        # Poll without the capability — should get nothing
        result = await poll.execute(
            PollTaskInput(worker_id="w1", capabilities=["file-access"])
        )
        assert result.is_ok
        assert result.value is None

        # Poll with the capability — should get the task
        result = await poll.execute(
            PollTaskInput(worker_id="w2", capabilities=["browse-as-me"])
        )
        assert result.is_ok
        assert result.value is not None
        assert result.value.type == "browser"
