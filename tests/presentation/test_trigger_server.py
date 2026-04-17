"""Contract tests for metis-trigger tools — input/output format validation.

Response shapes follow the MCP async-tasks spec (2025-11-25):
    enqueue -> {"task": {"id", "status": "working"}}
    get_result -> {"task": {"id", "status": <spec-status>}, "result"|"error": ..., "metis"?: ...}
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from metis.presentation.trigger_tools import register_trigger_tools


class TestEnqueueFormat:
    """Verify enqueue() returns a spec-compliant task envelope."""

    async def test_should_return_task_envelope(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        mcp = FastMCP("test")
        handle = register_trigger_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            result = await handle.enqueue(
                type="classify",
                payload={"text": "hello"},
            )

        assert "task" in result
        assert len(result["task"]["id"]) == 36  # UUID format
        assert result["task"]["status"] == "working"


class TestGetResultFormat:
    """Verify get_result() returns the correct format."""

    async def test_should_return_working_on_timeout(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        mcp = FastMCP("test")
        handle = register_trigger_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            enqueue_result = await handle.enqueue(type="test", payload={})
            result = await handle.get_result(
                task_id=enqueue_result["task"]["id"],
                timeout=0.3,
            )

        # On timeout, the task is still working — caller re-polls
        assert result["task"]["status"] == "working"

    async def test_should_return_error_envelope_for_invalid_task_id(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        mcp = FastMCP("test")
        handle = register_trigger_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            result = await handle.get_result(task_id="not-a-uuid", timeout=0.3)

        assert result["task"]["status"] == "failed"
        assert result["error"]["code"] == "INVALID_TASK_ID"


class TestCheckHealthFormat:
    """Verify check_health() returns the correct format."""

    async def test_should_return_false_when_no_heartbeats(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        mcp = FastMCP("test")
        handle = register_trigger_tools(mcp, db_path=db_path)

        async with handle.lifespan(mcp):
            result = await handle.check_health()

        assert result == {"worker_alive": False}
