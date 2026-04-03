"""Contract tests for metis-trigger MCP server — input/output format validation."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch


class TestEnqueueFormat:
    """Verify enqueue() returns the correct format."""

    async def test_should_return_task_id(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        with patch.dict(os.environ, {"METIS_DB_PATH": db_path}):
            from metis.presentation.trigger_server import enqueue, lifespan, mcp

            async with lifespan(mcp):
                result = await enqueue(
                    type="classify",
                    payload={"text": "hello"},
                )

        assert "task_id" in result
        assert len(result["task_id"]) == 36  # UUID format


class TestGetResultFormat:
    """Verify get_result() returns the correct format."""

    async def test_should_return_timeout_when_no_worker(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        with patch.dict(os.environ, {"METIS_DB_PATH": db_path}):
            from metis.presentation.trigger_server import (
                enqueue,
                get_result,
                lifespan,
                mcp,
            )

            async with lifespan(mcp):
                enqueue_result = await enqueue(type="test", payload={})
                result = await get_result(
                    task_id=enqueue_result["task_id"],
                    timeout=0.3,
                )

        assert result["status"] == "timeout"

    async def test_should_return_error_for_invalid_task_id(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        with patch.dict(os.environ, {"METIS_DB_PATH": db_path}):
            from metis.presentation.trigger_server import get_result, lifespan, mcp

            async with lifespan(mcp):
                result = await get_result(task_id="not-a-uuid", timeout=0.3)

        assert result["status"] == "error"
        assert "message" in result


class TestCheckHealthFormat:
    """Verify check_health() returns the correct format."""

    async def test_should_return_false_when_no_heartbeats(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        with patch.dict(os.environ, {"METIS_DB_PATH": db_path}):
            from metis.presentation.trigger_server import check_health, lifespan, mcp

            async with lifespan(mcp):
                result = await check_health()

        assert result == {"worker_alive": False}
