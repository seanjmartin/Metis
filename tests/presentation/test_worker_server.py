"""Contract tests for metis-worker MCP server — input/output format validation."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from metis.domain.entities import Task
from metis.domain.value_objects import TaskId, TaskPriority


class TestPollResponseFormat:
    """Verify poll() returns the correct minimal-token format."""

    async def test_should_return_empty_when_no_tasks(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        with patch.dict(os.environ, {"METIS_DB_PATH": db_path}):
            from metis.presentation.worker_server import lifespan, mcp, poll

            async with lifespan(mcp):
                result = await poll(worker_id="w1")

        assert result == {"s": "e"}

    async def test_should_return_task_when_available(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        with patch.dict(os.environ, {"METIS_DB_PATH": db_path}):
            from metis.presentation.worker_server import lifespan, mcp, poll

            async with lifespan(mcp):
                from datetime import UTC, datetime

                from metis.infrastructure.database import init_async_database
                from metis.infrastructure.sqlite_task_store import SqliteTaskStore

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

                result = await poll(worker_id="w1")

        assert result["s"] == "t"
        assert "id" in result
        assert result["type"] == "classify"
        assert result["payload"] == {"text": "hello"}


class TestDeliverResponseFormat:
    """Verify deliver() returns the correct format."""

    async def test_should_return_error_for_missing_task(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        with patch.dict(os.environ, {"METIS_DB_PATH": db_path}):
            from metis.presentation.worker_server import deliver, lifespan, mcp

            async with lifespan(mcp):
                result = await deliver(
                    task_id="00000000-0000-0000-0000-000000000000",
                    result={"x": 1},
                )

        assert result["s"] == "err"
        assert "message" in result
