"""Tests for embeddable worker tools registration."""

from __future__ import annotations

from pathlib import Path

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
