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


class TestSessionIsolation:
    async def test_rejects_cancel_from_different_session(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        """Cancel with a session_id that doesn't match the task's must fail as 'not found'."""
        from metis.application.enqueue_task import EnqueueTaskInput as EnqInp

        store = SqliteTaskStore(db_conn)
        enqueue = EnqueueTaskUseCase(task_store=store)
        tid = (await enqueue.execute(EnqInp(type="t", payload={}, session_id="alice"))).value

        use_case = CancelTaskUseCase(task_store=store)
        result = await use_case.execute(CancelTaskInput(task_id=tid.value, session_id="bob"))

        assert result.is_error
        assert isinstance(result.error, TaskNotFoundError)  # no enumeration leak

        # Task is still non-terminal — Alice can still cancel it
        still = await store.get(tid)
        assert still is not None
        assert still.status == TaskStatus.PENDING

    async def test_allows_cancel_when_session_matches(self, db_conn: aiosqlite.Connection) -> None:
        from metis.application.enqueue_task import EnqueueTaskInput as EnqInp

        store = SqliteTaskStore(db_conn)
        enqueue = EnqueueTaskUseCase(task_store=store)
        tid = (await enqueue.execute(EnqInp(type="t", payload={}, session_id="alice"))).value

        use_case = CancelTaskUseCase(task_store=store)
        result = await use_case.execute(CancelTaskInput(task_id=tid.value, session_id="alice"))

        assert result.is_ok
        cancelled = await store.get(tid)
        assert cancelled is not None
        assert cancelled.status == TaskStatus.CANCELLED

    async def test_allows_cancel_when_no_session_id_supplied(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        """Single-tenant case: no session check when caller doesn't supply one."""
        from metis.application.enqueue_task import EnqueueTaskInput as EnqInp

        store = SqliteTaskStore(db_conn)
        enqueue = EnqueueTaskUseCase(task_store=store)
        tid = (await enqueue.execute(EnqInp(type="t", payload={}, session_id="alice"))).value

        use_case = CancelTaskUseCase(task_store=store)
        result = await use_case.execute(CancelTaskInput(task_id=tid.value))

        assert result.is_ok
