"""Tests for ReportProgressUseCase."""

from __future__ import annotations

import aiosqlite

from metis.application.cancel_task import CancelTaskInput, CancelTaskUseCase
from metis.application.enqueue_task import EnqueueTaskInput, EnqueueTaskUseCase
from metis.application.report_progress import (
    ReportProgressInput,
    ReportProgressUseCase,
)
from metis.domain.errors import TaskAlreadyTerminalError, TaskNotFoundError
from metis.domain.value_objects import WorkerId
from metis.infrastructure.sqlite_progress_store import SqliteProgressStore
from metis.infrastructure.sqlite_task_store import SqliteTaskStore


class TestReportProgress:
    async def test_records_progress_for_claimed_task(self, db_conn: aiosqlite.Connection) -> None:
        task_store = SqliteTaskStore(db_conn)
        prog_store = SqliteProgressStore(db_conn)
        enq = EnqueueTaskUseCase(task_store=task_store)
        use_case = ReportProgressUseCase(task_store=task_store, progress_store=prog_store)

        tid = (await enq.execute(EnqueueTaskInput(type="t", payload={}))).value
        await task_store.claim_next([], WorkerId(value="w1"))

        r1 = await use_case.execute(
            ReportProgressInput(task_id=tid.value, progress=0.25, message="quarter")
        )
        r2 = await use_case.execute(
            ReportProgressInput(
                task_id=tid.value, progress=0.75, total=1.0, message="three quarters"
            )
        )

        assert r1.is_ok
        assert r1.value == 1
        assert r2.is_ok
        assert r2.value == 2

        updates = await prog_store.tail_since(tid, last_seq=0)
        assert len(updates) == 2
        assert updates[0].progress == 0.25
        assert updates[1].total == 1.0

    async def test_rejects_unknown_task(self, db_conn: aiosqlite.Connection) -> None:
        task_store = SqliteTaskStore(db_conn)
        prog_store = SqliteProgressStore(db_conn)
        use_case = ReportProgressUseCase(task_store=task_store, progress_store=prog_store)

        from metis.domain.value_objects import TaskId

        result = await use_case.execute(
            ReportProgressInput(task_id=TaskId.generate().value, progress=0.5)
        )
        assert result.is_error
        assert isinstance(result.error, TaskNotFoundError)

    async def test_rejects_terminal_task(self, db_conn: aiosqlite.Connection) -> None:
        task_store = SqliteTaskStore(db_conn)
        prog_store = SqliteProgressStore(db_conn)
        enq = EnqueueTaskUseCase(task_store=task_store)
        cancel = CancelTaskUseCase(task_store=task_store)
        use_case = ReportProgressUseCase(task_store=task_store, progress_store=prog_store)

        tid = (await enq.execute(EnqueueTaskInput(type="t", payload={}))).value
        await cancel.execute(CancelTaskInput(task_id=tid.value))

        result = await use_case.execute(ReportProgressInput(task_id=tid.value, progress=0.5))
        assert result.is_error
        assert isinstance(result.error, TaskAlreadyTerminalError)
