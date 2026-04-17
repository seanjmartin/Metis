"""Tests for CancelTaskUseCase — integration with real SQLite."""

from __future__ import annotations

import aiosqlite

from metis.application.cancel_task import CancelTaskInput, CancelTaskUseCase
from metis.application.enqueue_task import EnqueueTaskInput, EnqueueTaskUseCase
from metis.domain.errors import TaskAlreadyTerminalError, TaskNotFoundError
from metis.domain.value_objects import TaskId, TaskStatus, WorkerId
from metis.infrastructure.sqlite_task_store import SqliteTaskStore


async def _enqueue(store: SqliteTaskStore) -> TaskId:
    use_case = EnqueueTaskUseCase(task_store=store)
    result = await use_case.execute(EnqueueTaskInput(type="test", payload={"k": "v"}))
    assert result.is_ok
    return result.value


class TestCancelTask:
    async def test_cancels_pending_task(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        tid = await _enqueue(store)

        use_case = CancelTaskUseCase(task_store=store)
        result = await use_case.execute(CancelTaskInput(task_id=tid.value))

        assert result.is_ok
        task = await store.get(tid)
        assert task is not None
        assert task.status == TaskStatus.CANCELLED
        assert task.cancelled_at is not None

    async def test_cancels_claimed_task(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        tid = await _enqueue(store)
        claimed = await store.claim_next([], WorkerId(value="w1"))
        assert claimed is not None

        use_case = CancelTaskUseCase(task_store=store)
        result = await use_case.execute(CancelTaskInput(task_id=tid.value))

        assert result.is_ok
        task = await store.get(tid)
        assert task is not None
        assert task.status == TaskStatus.CANCELLED

    async def test_rejects_unknown_task(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        use_case = CancelTaskUseCase(task_store=store)
        result = await use_case.execute(CancelTaskInput(task_id=TaskId.generate().value))

        assert result.is_error
        assert isinstance(result.error, TaskNotFoundError)

    async def test_rejects_already_cancelled(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        tid = await _enqueue(store)
        use_case = CancelTaskUseCase(task_store=store)
        first = await use_case.execute(CancelTaskInput(task_id=tid.value))
        assert first.is_ok

        second = await use_case.execute(CancelTaskInput(task_id=tid.value))
        assert second.is_error
        assert isinstance(second.error, TaskAlreadyTerminalError)

    async def test_rejects_completed_task(self, db_conn: aiosqlite.Connection) -> None:
        """COMPLETE is not .is_terminal, but CANCELLED isn't a valid next state from COMPLETE."""
        store = SqliteTaskStore(db_conn)
        tid = await _enqueue(store)
        claimed = await store.claim_next([], WorkerId(value="w1"))
        assert claimed is not None
        claimed.complete({"x": 1})
        await store.update(claimed)

        use_case = CancelTaskUseCase(task_store=store)
        result = await use_case.execute(CancelTaskInput(task_id=tid.value))

        assert result.is_error
        assert isinstance(result.error, TaskAlreadyTerminalError)
