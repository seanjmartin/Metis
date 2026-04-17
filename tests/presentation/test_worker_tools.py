"""Tests for embeddable worker tools registration."""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from metis.presentation.worker_tools import register_worker_tools


class TestRegisterWorkerTools:
    def test_should_return_handle_with_all_tools(self, tmp_path: Path) -> None:
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=str(tmp_path / "test.db"))

        assert callable(handle.poll)
        assert callable(handle.deliver)
        assert callable(handle.probe)
        assert callable(handle.lifespan)

    def test_should_accept_session_id_string(self, tmp_path: Path) -> None:
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=str(tmp_path / "test.db"), session_id="alice")
        assert callable(handle.poll)

    def test_should_accept_session_id_callable(self, tmp_path: Path) -> None:
        mcp = FastMCP("test")
        handle = register_worker_tools(
            mcp, db_path=str(tmp_path / "test.db"), session_id=lambda: "bob"
        )
        assert callable(handle.poll)

    def test_should_not_conflict_with_host_tools(self, tmp_path: Path) -> None:
        mcp = FastMCP("test", warn_on_duplicate_tools=False)

        @mcp.tool()
        async def my_tool() -> str:
            """A host tool."""
            return "hello"

        register_worker_tools(mcp, db_path=str(tmp_path / "test.db"))

        # Both host and metis tools should be registered
        tool_names = [t.name for t in mcp._tool_manager.list_tools()]
        assert "my_tool" in tool_names
        assert "poll" in tool_names
        assert "deliver" in tool_names
        assert "probe" in tool_names


class TestRequireLifespan:
    async def test_poll_should_raise_runtime_error_when_lifespan_not_entered(
        self, tmp_path: Path
    ) -> None:
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=str(tmp_path / "test.db"))

        with pytest.raises(RuntimeError, match="metis worker tools not initialized"):
            await handle.poll()

    async def test_deliver_should_raise_runtime_error_when_lifespan_not_entered(
        self, tmp_path: Path
    ) -> None:
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=str(tmp_path / "test.db"))

        with pytest.raises(RuntimeError, match="metis worker tools not initialized"):
            await handle.deliver(task_id="x", result={})

    async def test_poll_should_succeed_after_lifespan_entered(self, tmp_path: Path) -> None:
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=str(tmp_path / "test.db"))

        async with handle.lifespan(mcp):
            response = await handle.poll(worker_id="w1", timeout=0)

        assert response == {"s": "e"}


class TestReportProgressTool:
    async def test_rejects_unknown_task(self, tmp_path: Path) -> None:
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=str(tmp_path / "test.db"))

        async with handle.lifespan(mcp):
            result = await handle.report_progress(
                task_id="00000000-0000-0000-0000-000000000000",
                progress=0.5,
                message="halfway",
            )

        assert result["s"] == "err"
        assert result["code"] == "TASK_NOT_FOUND"

    async def test_records_progress_for_claimed_task(self, tmp_path: Path) -> None:
        from metis import TaskQueue
        from metis.domain.value_objects import TaskId, WorkerId
        from metis.infrastructure.database import init_async_database
        from metis.infrastructure.sqlite_task_store import SqliteTaskStore

        db_path = str(tmp_path / "p.db")
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            # Enqueue + claim via a separate queue
            q = TaskQueue(db_path=db_path)
            tid = q.enqueue(type="t", payload={})
            q.close()

            conn = await init_async_database(db_path)
            try:
                store = SqliteTaskStore(conn)
                claimed = await store.claim_next([], WorkerId(value="w1"))
                assert claimed is not None
            finally:
                await conn.close()

            result = await handle.report_progress(
                task_id=tid.value,
                progress=0.5,
                message="halfway",
            )

        assert result["s"] == "ok"
        assert result["seq"] == 1
        # Reference the TaskId import so ruff doesn't flag it
        assert TaskId


class TestCheckCancelledTool:
    async def test_returns_false_for_claimed_task(self, tmp_path: Path) -> None:
        from metis import TaskQueue
        from metis.domain.value_objects import WorkerId
        from metis.infrastructure.database import init_async_database
        from metis.infrastructure.sqlite_task_store import SqliteTaskStore

        db_path = str(tmp_path / "c.db")
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            q = TaskQueue(db_path=db_path)
            tid = q.enqueue(type="t", payload={})
            q.close()

            conn = await init_async_database(db_path)
            try:
                await SqliteTaskStore(conn).claim_next([], WorkerId(value="w1"))
            finally:
                await conn.close()

            result = await handle.check_cancelled(task_id=tid.value)

        assert result["cancelled"] is False
        assert result["status"] == "working"

    async def test_returns_true_after_cancel(self, tmp_path: Path) -> None:
        import asyncio

        from metis import TaskQueue

        db_path = str(tmp_path / "cc.db")
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            q = TaskQueue(db_path=db_path)
            tid = q.enqueue(type="t", payload={})
            q.close()

            q2 = TaskQueue(db_path=db_path)
            await q2.cancel(tid)
            q2.close()

            # allow any filesystem settle
            await asyncio.sleep(0.05)

            result = await handle.check_cancelled(task_id=tid.value)

        assert result["cancelled"] is True
        assert result["status"] == "cancelled"


async def _claim(db_path: str, task_id_value: str) -> None:
    """Helper: claim the task via the worker store so we can request_input on it."""
    from metis.domain.value_objects import WorkerId
    from metis.infrastructure.database import init_async_database
    from metis.infrastructure.sqlite_task_store import SqliteTaskStore

    conn = await init_async_database(db_path)
    try:
        claimed = await SqliteTaskStore(conn).claim_next([], WorkerId(value="w1"))
        assert claimed is not None
        assert claimed.id.value == task_id_value
    finally:
        await conn.close()


class TestRequestInputTool:
    async def test_request_input_transitions_task_to_input_required(self, tmp_path: Path) -> None:
        from metis import TaskQueue

        db_path = str(tmp_path / "ri.db")
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            q = TaskQueue(db_path=db_path)
            tid = q.enqueue(type="t", payload={})
            q.close()

            await _claim(db_path, tid.value)

            response = await handle.request_input(
                task_id=tid.value,
                prompt="What category?",
                schema={"type": "object"},
            )

        assert response["s"] == "ok"
        assert response["seq"] == 1

        # And the recorded task status is INPUT_REQUIRED
        from metis.domain.value_objects import TaskStatus

        q2 = TaskQueue(db_path=db_path)
        try:
            assert q2.get_task_status(tid) == TaskStatus.INPUT_REQUIRED
        finally:
            q2.close()

    async def test_request_input_rejects_unknown_task(self, tmp_path: Path) -> None:
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=str(tmp_path / "ri2.db"))

        async with handle.lifespan(mcp):
            response = await handle.request_input(
                task_id="00000000-0000-0000-0000-000000000000",
                prompt="q",
                schema={},
            )

        assert response["s"] == "err"
        assert response["code"] == "TASK_NOT_FOUND"

    async def test_request_input_rejects_terminal_task(self, tmp_path: Path) -> None:
        from metis import TaskQueue

        db_path = str(tmp_path / "ri3.db")
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            q = TaskQueue(db_path=db_path)
            tid = q.enqueue(type="t", payload={})
            await q.cancel(tid)
            q.close()

            response = await handle.request_input(
                task_id=tid.value,
                prompt="q",
                schema={},
            )

        assert response["s"] == "err"
        assert response["code"] == "TASK_ALREADY_TERMINAL"


class TestAwaitInputResponse:
    async def test_returns_response_when_provided(self, tmp_path: Path) -> None:
        import asyncio

        from metis import TaskQueue

        db_path = str(tmp_path / "ai.db")
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            q = TaskQueue(db_path=db_path)
            tid = q.enqueue(type="t", payload={})
            q.close()

            await _claim(db_path, tid.value)
            r = await handle.request_input(task_id=tid.value, prompt="q", schema={"type": "object"})
            seq = r["seq"]

            async def provide_shortly() -> None:
                await asyncio.sleep(0.2)
                q2 = TaskQueue(db_path=db_path)
                await q2.provide_input(tid, {"answer": "yes"})
                q2.close()

            writer = asyncio.create_task(provide_shortly())
            response = await handle.await_input_response(task_id=tid.value, seq=seq, timeout=5.0)
            await writer

        assert response["s"] == "resp"
        assert response["response"] == {"answer": "yes"}

    async def test_returns_timeout_when_no_response(self, tmp_path: Path) -> None:
        from metis import TaskQueue

        db_path = str(tmp_path / "ait.db")
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            q = TaskQueue(db_path=db_path)
            tid = q.enqueue(type="t", payload={})
            q.close()

            await _claim(db_path, tid.value)
            r = await handle.request_input(task_id=tid.value, prompt="q", schema={})
            response = await handle.await_input_response(
                task_id=tid.value, seq=r["seq"], timeout=0.5
            )

        assert response["s"] == "timeout"

    async def test_returns_cancelled_when_task_cancelled_mid_wait(self, tmp_path: Path) -> None:
        import asyncio

        from metis import TaskQueue

        db_path = str(tmp_path / "aic.db")
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            q = TaskQueue(db_path=db_path)
            tid = q.enqueue(type="t", payload={})
            q.close()

            await _claim(db_path, tid.value)
            r = await handle.request_input(task_id=tid.value, prompt="q", schema={})

            async def cancel_shortly() -> None:
                await asyncio.sleep(0.2)
                q2 = TaskQueue(db_path=db_path)
                await q2.cancel(tid)
                q2.close()

            canceller = asyncio.create_task(cancel_shortly())
            response = await handle.await_input_response(
                task_id=tid.value, seq=r["seq"], timeout=5.0
            )
            await canceller

        assert response["s"] == "cancelled"


class TestRequestSamplingTool:
    async def test_request_sampling_uses_sentinel_prompt_schema(self, tmp_path: Path) -> None:
        from metis import TaskQueue
        from metis.domain.value_objects import TaskStatus

        db_path = str(tmp_path / "rs.db")
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            q = TaskQueue(db_path=db_path)
            tid = q.enqueue(type="t", payload={})
            q.close()

            await _claim(db_path, tid.value)

            response = await handle.request_sampling(
                task_id=tid.value,
                messages=[{"role": "user", "content": "hi"}],
                system="be brief",
                max_tokens=100,
            )

        assert response["s"] == "ok"

        # Inspect what was stored
        from metis.domain.value_objects import TaskId as _TaskId
        from metis.infrastructure.database import init_async_database
        from metis.infrastructure.sqlite_task_store import SqliteTaskStore

        conn = await init_async_database(db_path)
        try:
            task = await SqliteTaskStore(conn).get(_TaskId(value=tid.value))
            assert task is not None
            assert task.status == TaskStatus.INPUT_REQUIRED
            assert task.input_prompt == "__metis_sampling__"
            assert task.input_schema is not None
            assert task.input_schema["_metis_sampling"] is True
            assert task.input_schema["messages"] == [{"role": "user", "content": "hi"}]
            assert task.input_schema["system"] == "be brief"
        finally:
            await conn.close()
