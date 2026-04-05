"""Tests for embeddable trigger tools registration."""

from __future__ import annotations

from pathlib import Path

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
