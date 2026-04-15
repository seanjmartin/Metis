"""Contract tests for metis-worker tools — input/output format validation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from metis.domain.entities import Task
from metis.domain.value_objects import TaskId, TaskPriority
from metis.infrastructure.database import init_async_database
from metis.infrastructure.sqlite_task_store import SqliteTaskStore
from metis.presentation.worker_tools import register_worker_tools


class TestPollResponseFormat:
    """Verify poll() returns the correct minimal-token format."""

    async def test_should_return_empty_when_no_tasks(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            result = await handle.poll(worker_id="w1")

        assert result == {"s": "e"}

    async def test_should_return_task_when_available(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            conn = await init_async_database(db_path)
            store = SqliteTaskStore(conn)
            task = Task(
                id=TaskId.generate(),
                type="classify",
                payload={"text": "hello"},
                priority=TaskPriority(value=0),
                created_at=datetime.now(UTC),
            )
            await store.insert(task)
            await conn.close()

            result = await handle.poll(worker_id="w1")

        assert result["s"] == "t"
        assert "id" in result
        assert result["type"] == "classify"
        assert result["payload"] == {"text": "hello"}
        assert "sid" not in result  # No session_id set

    async def test_should_include_sid_when_session_id_set(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            conn = await init_async_database(db_path)
            store = SqliteTaskStore(conn)
            task = Task(
                id=TaskId.generate(),
                type="classify",
                payload={"text": "hello"},
                priority=TaskPriority(value=0),
                session_id="alice",
                created_at=datetime.now(UTC),
            )
            await store.insert(task)
            await conn.close()

            result = await handle.poll(worker_id="w1")

        assert result["s"] == "t"
        assert result["sid"] == "alice"


class TestDeliverResponseFormat:
    """Verify deliver() returns the correct format."""

    async def test_should_return_error_for_missing_task(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        mcp = FastMCP("test")
        handle = register_worker_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            result = await handle.deliver(
                task_id="00000000-0000-0000-0000-000000000000",
                result={"x": 1},
            )

        assert result["s"] == "err"
        assert "message" in result
