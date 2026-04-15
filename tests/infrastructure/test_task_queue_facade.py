"""Tests for the TaskQueue public facade — the deep module."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from metis import TaskQueue
from metis.domain.value_objects import TaskId


class TestEnqueue:
    def test_should_return_task_id(self, tmp_path: Path) -> None:
        queue = TaskQueue(db_path=str(tmp_path / "test.db"))
        task_id = queue.enqueue(type="classify", payload={"text": "hello"})
        assert isinstance(task_id, TaskId)
        queue.close()

    def test_should_accept_custom_priority_and_ttl(self, tmp_path: Path) -> None:
        queue = TaskQueue(db_path=str(tmp_path / "test.db"))
        task_id = queue.enqueue(type="validate", payload={}, priority=10, ttl_seconds=60)
        assert isinstance(task_id, TaskId)
        queue.close()

    def test_should_accept_session_id(self, tmp_path: Path) -> None:
        queue = TaskQueue(db_path=str(tmp_path / "test.db"))
        task_id = queue.enqueue(type="classify", payload={"text": "hello"}, session_id="alice")
        assert isinstance(task_id, TaskId)

        # Verify session_id was stored
        conn = queue._get_sync_conn()
        row = conn.execute("SELECT session_id FROM tasks WHERE id = ?", (task_id.value,)).fetchone()
        assert row["session_id"] == "alice"
        queue.close()


class TestWaitForResult:
    async def test_should_return_none_on_timeout(self, tmp_path: Path) -> None:
        queue = TaskQueue(db_path=str(tmp_path / "test.db"))
        task_id = queue.enqueue(type="test", payload={})
        result = await queue.wait_for_result(task_id, timeout=0.3)
        assert result is None
        queue.close()

    async def test_should_raise_on_expired_task(self, tmp_path: Path) -> None:
        queue = TaskQueue(db_path=str(tmp_path / "test.db"))
        task_id = queue.enqueue(type="test", payload={}, ttl_seconds=0)

        # Expire the task via a separate enqueue (triggers expire_stale)
        await asyncio.sleep(0.1)
        queue.enqueue(type="dummy", payload={})

        # Now mark it as expired in DB directly
        conn = queue._get_sync_conn()
        conn.execute("UPDATE tasks SET status = 'expired' WHERE id = ?", (task_id.value,))
        conn.commit()

        with pytest.raises(ValueError, match="TASK_EXPIRED"):
            await queue.wait_for_result(task_id, timeout=1.0)
        queue.close()


class TestIsWorkerAlive:
    def test_should_return_false_when_no_heartbeats(self, tmp_path: Path) -> None:
        queue = TaskQueue(db_path=str(tmp_path / "test.db"))
        assert queue.is_worker_alive() is False
        queue.close()

    def test_should_return_true_after_heartbeat(self, tmp_path: Path) -> None:
        queue = TaskQueue(db_path=str(tmp_path / "test.db"))

        # Insert a heartbeat manually
        import json
        from datetime import UTC, datetime

        conn = queue._get_sync_conn()
        conn.execute(
            "INSERT INTO heartbeats (worker_id, capabilities, last_seen) VALUES (?, ?, ?)",
            ("w1", json.dumps([]), datetime.now(UTC).isoformat()),
        )
        conn.commit()

        assert queue.is_worker_alive() is True
        queue.close()


class TestClose:
    def test_should_close_connection(self, tmp_path: Path) -> None:
        queue = TaskQueue(db_path=str(tmp_path / "test.db"))
        queue.enqueue(type="test", payload={})
        queue.close()
        assert queue._sync_conn is None

    def test_should_be_safe_to_call_twice(self, tmp_path: Path) -> None:
        queue = TaskQueue(db_path=str(tmp_path / "test.db"))
        queue.close()
        queue.close()  # Should not raise
