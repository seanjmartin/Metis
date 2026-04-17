"""Tests for ProvideInputUseCase — the write-back half of the elicitation round-trip."""

from __future__ import annotations

import aiosqlite

from metis.application.cancel_task import CancelTaskInput, CancelTaskUseCase
from metis.application.enqueue_task import EnqueueTaskInput, EnqueueTaskUseCase
from metis.application.provide_input import ProvideInputInput, ProvideInputUseCase
from metis.domain.errors import (
    InvalidTransitionError,
    TaskAlreadyTerminalError,
    TaskNotFoundError,
)
from metis.domain.value_objects import TaskId, TaskStatus, WorkerId
from metis.infrastructure.sqlite_task_store import SqliteTaskStore


async def _make_waiting_task(store: SqliteTaskStore) -> TaskId:
    """Enqueue + claim + transition to INPUT_REQUIRED, returning the task id."""
    enqueue = EnqueueTaskUseCase(task_store=store)
    tid = (await enqueue.execute(EnqueueTaskInput(type="t", payload={}))).value

    claimed = await store.claim_next([], WorkerId(value="w1"))
    assert claimed is not None
    claimed.request_input("pick one", {"type": "object"})
    await store.update(claimed)
    return tid


class TestProvideInput:
    async def test_transitions_input_required_to_claimed(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        store = SqliteTaskStore(db_conn)
        tid = await _make_waiting_task(store)

        use_case = ProvideInputUseCase(task_store=store)
        result = await use_case.execute(
            ProvideInputInput(task_id=tid.value, response={"choice": "a"})
        )

        assert result.is_ok
        task = await store.get(tid)
        assert task is not None
        assert task.status == TaskStatus.CLAIMED
        assert task.input_response == {"choice": "a"}

    async def test_rejects_when_task_missing(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        use_case = ProvideInputUseCase(task_store=store)
        result = await use_case.execute(
            ProvideInputInput(task_id=TaskId.generate().value, response={})
        )
        assert result.is_error
        assert isinstance(result.error, TaskNotFoundError)

    async def test_rejects_when_task_is_terminal(self, db_conn: aiosqlite.Connection) -> None:
        """Cancelling mid-elicitation then trying to provide_input must reject."""
        store = SqliteTaskStore(db_conn)
        tid = await _make_waiting_task(store)
        cancel = CancelTaskUseCase(task_store=store)
        await cancel.execute(CancelTaskInput(task_id=tid.value))

        use_case = ProvideInputUseCase(task_store=store)
        result = await use_case.execute(ProvideInputInput(task_id=tid.value, response={}))
        assert result.is_error
        assert isinstance(result.error, TaskAlreadyTerminalError)

    async def test_rejects_when_not_awaiting_input(self, db_conn: aiosqlite.Connection) -> None:
        """If the task is CLAIMED (not INPUT_REQUIRED), provide_input is invalid."""
        store = SqliteTaskStore(db_conn)
        enqueue = EnqueueTaskUseCase(task_store=store)
        tid = (await enqueue.execute(EnqueueTaskInput(type="t", payload={}))).value
        await store.claim_next([], WorkerId(value="w1"))  # CLAIMED but no request_input

        use_case = ProvideInputUseCase(task_store=store)
        result = await use_case.execute(ProvideInputInput(task_id=tid.value, response={"x": 1}))
        assert result.is_error
        assert isinstance(result.error, InvalidTransitionError)

    async def test_multi_round_provides_update_input_response(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        """Second request_input + provide_input should overwrite the response."""
        store = SqliteTaskStore(db_conn)
        tid = await _make_waiting_task(store)
        use_case = ProvideInputUseCase(task_store=store)

        first = await use_case.execute(ProvideInputInput(task_id=tid.value, response={"r": 1}))
        assert first.is_ok

        # Dispatcher asks a second question
        task = await store.get(tid)
        assert task is not None
        task.request_input("round 2", {"type": "object"})
        await store.update(task)

        second = await use_case.execute(ProvideInputInput(task_id=tid.value, response={"r": 2}))
        assert second.is_ok

        final = await store.get(tid)
        assert final is not None
        assert final.input_response == {"r": 2}
        assert final.input_seq == 2
