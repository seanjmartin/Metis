"""Tests for embeddable trigger tools registration."""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from metis.presentation.trigger_tools import register_trigger_tools


class TestRegisterTriggerTools:
    def test_should_return_handle_with_all_tools(self, tmp_path: Path) -> None:
        mcp = FastMCP("test")
        handle = register_trigger_tools(mcp, db_path=str(tmp_path / "test.db"))

        assert callable(handle.enqueue)
        assert callable(handle.get_result)
        assert callable(handle.check_health)
        assert callable(handle.lifespan)

    def test_should_accept_session_id_string(self, tmp_path: Path) -> None:
        mcp = FastMCP("test")
        handle = register_trigger_tools(mcp, db_path=str(tmp_path / "test.db"), session_id="alice")
        assert callable(handle.enqueue)

    def test_should_accept_session_id_callable(self, tmp_path: Path) -> None:
        mcp = FastMCP("test")
        handle = register_trigger_tools(
            mcp, db_path=str(tmp_path / "test.db"), session_id=lambda: "bob"
        )
        assert callable(handle.enqueue)

    def test_should_not_conflict_with_host_tools(self, tmp_path: Path) -> None:
        mcp = FastMCP("test", warn_on_duplicate_tools=False)

        @mcp.tool()
        async def my_tool() -> str:
            """A host tool."""
            return "hello"

        register_trigger_tools(mcp, db_path=str(tmp_path / "test.db"))

        tool_names = [t.name for t in mcp._tool_manager.list_tools()]
        assert "my_tool" in tool_names
        assert "enqueue" in tool_names
        assert "get_result" in tool_names
        assert "check_health" in tool_names


class TestRequireLifespan:
    async def test_enqueue_should_raise_runtime_error_when_lifespan_not_entered(
        self, tmp_path: Path
    ) -> None:
        mcp = FastMCP("test")
        handle = register_trigger_tools(mcp, db_path=str(tmp_path / "test.db"))

        with pytest.raises(RuntimeError, match="metis trigger tools not initialized"):
            await handle.enqueue(type="classify", payload={})

    async def test_check_health_should_raise_when_lifespan_not_entered(
        self, tmp_path: Path
    ) -> None:
        mcp = FastMCP("test")
        handle = register_trigger_tools(mcp, db_path=str(tmp_path / "test.db"))

        with pytest.raises(RuntimeError, match="metis trigger tools not initialized"):
            await handle.check_health()

    async def test_enqueue_should_succeed_after_lifespan_entered(self, tmp_path: Path) -> None:
        mcp = FastMCP("test")
        handle = register_trigger_tools(mcp, db_path=str(tmp_path / "test.db"))

        async with handle.lifespan(mcp):
            response = await handle.enqueue(type="classify", payload={"text": "hi"})

        assert response["task"]["status"] == "working"
        assert len(response["task"]["id"]) == 36


class TestCancelTool:
    async def test_cancel_pending_task(self, tmp_path: Path) -> None:
        mcp = FastMCP("test")
        handle = register_trigger_tools(mcp, db_path=str(tmp_path / "test.db"))

        async with handle.lifespan(mcp):
            enq = await handle.enqueue(type="test", payload={})
            result = await handle.cancel(task_id=enq["task"]["id"])

        assert result["task"]["status"] == "cancelled"

    async def test_cancel_rejects_terminal_task(self, tmp_path: Path) -> None:
        mcp = FastMCP("test")
        handle = register_trigger_tools(mcp, db_path=str(tmp_path / "test.db"))

        async with handle.lifespan(mcp):
            enq = await handle.enqueue(type="test", payload={})
            tid = enq["task"]["id"]
            await handle.cancel(task_id=tid)
            result = await handle.cancel(task_id=tid)

        assert "error" in result
        assert result["error"]["code"] == "TASK_ALREADY_TERMINAL"
        assert result["error"]["json_rpc_code"] == -32602

    async def test_cancel_invalid_task_id_returns_error(self, tmp_path: Path) -> None:
        mcp = FastMCP("test")
        handle = register_trigger_tools(mcp, db_path=str(tmp_path / "test.db"))

        async with handle.lifespan(mcp):
            result = await handle.cancel(task_id="not-a-uuid")

        assert result["error"]["code"] == "INVALID_TASK_ID"
