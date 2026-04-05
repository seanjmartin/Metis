"""Contract tests for metis-trigger tools — input/output format validation."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from metis.presentation.trigger_tools import register_trigger_tools


class TestEnqueueFormat:
    """Verify enqueue() returns the correct format."""

    async def test_should_return_task_id(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        mcp = FastMCP("test")
        handle = register_trigger_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            result = await handle.enqueue(
                type="classify",
                payload={"text": "hello"},
            )

        assert "task_id" in result
        assert len(result["task_id"]) == 36  # UUID format


class TestGetResultFormat:
    """Verify get_result() returns the correct format."""

    async def test_should_return_timeout_when_no_worker(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        mcp = FastMCP("test")
        handle = register_trigger_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            enqueue_result = await handle.enqueue(type="test", payload={})
            result = await handle.get_result(
                task_id=enqueue_result["task_id"],
                timeout=0.3,
            )

        assert result["status"] == "timeout"

    async def test_should_return_error_for_invalid_task_id(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        mcp = FastMCP("test")
        handle = register_trigger_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            result = await handle.get_result(task_id="not-a-uuid", timeout=0.3)

        assert result["status"] == "error"
        assert "message" in result


class TestCheckHealthFormat:
    """Verify check_health() returns the correct format."""

    async def test_should_return_false_when_no_heartbeats(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        mcp = FastMCP("test")
        handle = register_trigger_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            result = await handle.check_health()

        assert result == {"worker_alive": False}
