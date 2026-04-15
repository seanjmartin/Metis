"""Full round-trip POC proof: enqueue → poll → deliver → wait_for_result.

This is the key test that proves the Metis core works end-to-end.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from metis.application.check_health import CheckHealthUseCase
from metis.application.deliver_result import DeliverResultInput, DeliverResultUseCase
from metis.application.enqueue_task import EnqueueTaskInput, EnqueueTaskUseCase
from metis.application.poll_task import PollTaskInput, PollTaskUseCase
from metis.application.wait_for_result import WaitForResultInput, WaitForResultUseCase
from metis.infrastructure.sqlite_heartbeat_store import SqliteHeartbeatStore
from metis.infrastructure.sqlite_task_store import SqliteTaskStore


class TestBlockAndWaitRoundtrip:
    """Proves block-and-wait mode: enqueue, hold open, get result."""

    async def test_full_cycle(self, db_conn: aiosqlite.Connection) -> None:
        task_store = SqliteTaskStore(db_conn)
        hb_store = SqliteHeartbeatStore(db_conn)

        enqueue = EnqueueTaskUseCase(task_store=task_store)
        poll = PollTaskUseCase(task_store=task_store, heartbeat_store=hb_store)
        deliver = DeliverResultUseCase(task_store=task_store)
        wait = WaitForResultUseCase(task_store=task_store)
        health = CheckHealthUseCase(heartbeat_store=hb_store)

        # 1. No worker yet
        health_result = await health.execute()
        assert health_result.is_ok and health_result.value is False

        # 2. Enqueue a task
        enqueue_result = await enqueue.execute(
            EnqueueTaskInput(
                type="classify",
                payload={"text": "Where should I save this note?"},
                priority=5,
                ttl_seconds=60,
            )
        )
        assert enqueue_result.is_ok
        task_id_str = enqueue_result.value.value

        # 3. Simulate a worker: poll, process, deliver (with delay)
        async def simulate_worker() -> None:
            await asyncio.sleep(0.2)
            poll_result = await poll.execute(
                PollTaskInput(worker_id="dispatcher-1", capabilities=["classify"])
            )
            assert poll_result.is_ok
            task = poll_result.value
            assert task is not None
            assert task.type == "classify"

            # "Process" the task
            await deliver.execute(
                DeliverResultInput(
                    task_id=task.id.value,
                    result={"vault": "personal", "confidence": 0.95},
                )
            )

        worker = asyncio.create_task(simulate_worker())

        # 4. Wait for result (blocks until worker delivers)
        wait_result = await wait.execute(
            WaitForResultInput(task_id=task_id_str, timeout_seconds=5.0)
        )
        await worker

        assert wait_result.is_ok
        assert wait_result.value == {"vault": "personal", "confidence": 0.95}

        # 5. Worker should now show as alive
        health_result = await health.execute()
        assert health_result.is_ok and health_result.value is True


class TestFireAndForgetRoundtrip:
    """Proves fire-and-forget mode: enqueue, return immediately, check later."""

    async def test_enqueue_then_check_later(self, db_conn: aiosqlite.Connection) -> None:
        task_store = SqliteTaskStore(db_conn)
        hb_store = SqliteHeartbeatStore(db_conn)

        enqueue = EnqueueTaskUseCase(task_store=task_store)
        poll = PollTaskUseCase(task_store=task_store, heartbeat_store=hb_store)
        deliver = DeliverResultUseCase(task_store=task_store)
        wait = WaitForResultUseCase(task_store=task_store)

        # 1. Enqueue (fire-and-forget — caller doesn't wait)
        enqueue_result = await enqueue.execute(
            EnqueueTaskInput(type="refresh", payload={"vault": "finances"})
        )
        task_id_str = enqueue_result.value.value

        # 2. Worker picks up and completes
        poll_result = await poll.execute(PollTaskInput(worker_id="w1", capabilities=[]))
        assert poll_result.value is not None
        await deliver.execute(
            DeliverResultInput(
                task_id=poll_result.value.id.value,
                result={"refreshed": True, "documents": 12},
            )
        )

        # 3. Later, caller checks the result (short timeout — should be instant)
        wait_result = await wait.execute(
            WaitForResultInput(task_id=task_id_str, timeout_seconds=1.0)
        )

        assert wait_result.is_ok
        assert wait_result.value == {"refreshed": True, "documents": 12}
