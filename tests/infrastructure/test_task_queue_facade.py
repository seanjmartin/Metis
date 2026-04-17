"""Tests for the TaskQueue public facade — the deep module."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from metis import TaskQueue
from metis.domain.errors import MetisException, TaskExpiredError
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

    async def test_should_raise_typed_exception_on_expired_task(self, tmp_path: Path) -> None:
        queue = TaskQueue(db_path=str(tmp_path / "test.db"))
        task_id = queue.enqueue(type="test", payload={}, ttl_seconds=0)

        # Expire the task via a separate enqueue (triggers expire_stale)
        await asyncio.sleep(0.1)
        queue.enqueue(type="dummy", payload={})

        # Now mark it as expired in DB directly
        conn = queue._get_sync_conn()
        conn.execute("UPDATE tasks SET status = 'expired' WHERE id = ?", (task_id.value,))
        conn.commit()

        with pytest.raises(MetisException) as exc_info:
            await queue.wait_for_result(task_id, timeout=1.0)
        assert isinstance(exc_info.value.error, TaskExpiredError)
        assert exc_info.value.error.code == "TASK_EXPIRED"
        queue.close()

    async def test_should_raise_on_cancelled_task(self, tmp_path: Path) -> None:
        from metis.domain.errors import TaskCancelledError

        queue = TaskQueue(db_path=str(tmp_path / "test.db"))
        task_id = queue.enqueue(type="test", payload={})
        await queue.cancel(task_id)

        with pytest.raises(MetisException) as exc_info:
            await queue.wait_for_result(task_id, timeout=1.0)
        assert isinstance(exc_info.value.error, TaskCancelledError)
        assert exc_info.value.error.code == "TASK_CANCELLED"
        queue.close()

    async def test_should_raise_on_failed_task_with_error_details(self, tmp_path: Path) -> None:
        from metis.domain.errors import TaskFailedError

        queue = TaskQueue(db_path=str(tmp_path / "test.db"))
        task_id = queue.enqueue(type="test", payload={})

        # Mark the task FAILED directly with structured error details
        conn = queue._get_sync_conn()
        conn.execute(
            "UPDATE tasks SET status = 'failed', error_code = ?, error_message = ? WHERE id = ?",
            ("UPSTREAM_TIMEOUT", "upstream took too long", task_id.value),
        )
        conn.commit()

        with pytest.raises(MetisException) as exc_info:
            await queue.wait_for_result(task_id, timeout=1.0)
        assert isinstance(exc_info.value.error, TaskFailedError)
        assert "UPSTREAM_TIMEOUT" in exc_info.value.error.message
        assert "upstream took too long" in exc_info.value.error.message
        queue.close()

    async def test_cancel_respects_session_id_at_facade(self, tmp_path: Path) -> None:
        """Facade-level session_id filter on cancel."""
        from metis.domain.errors import TaskNotFoundError

        queue = TaskQueue(db_path=str(tmp_path / "test.db"))
        task_id = queue.enqueue(type="test", payload={}, session_id="alice")

        with pytest.raises(MetisException) as exc_info:
            await queue.cancel(task_id, session_id="bob")
        assert isinstance(exc_info.value.error, TaskNotFoundError)

        # Alice can still cancel
        await queue.cancel(task_id, session_id="alice")
        assert queue.get_task_status(task_id) is not None
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


class TestContextManager:
    def test_sync_context_manager_closes_on_exit(self, tmp_path: Path) -> None:
        with TaskQueue(db_path=str(tmp_path / "test.db")) as queue:
            queue.enqueue(type="test", payload={})
            assert queue._sync_conn is not None
        assert queue._sync_conn is None

    def test_sync_context_manager_closes_on_exception(self, tmp_path: Path) -> None:
        queue_ref: TaskQueue | None = None
        with (
            pytest.raises(RuntimeError),
            TaskQueue(db_path=str(tmp_path / "test.db")) as queue,
        ):
            queue_ref = queue
            queue.enqueue(type="test", payload={})
            raise RuntimeError("boom")
        assert queue_ref is not None
        assert queue_ref._sync_conn is None

    async def test_async_context_manager_closes_on_exit(self, tmp_path: Path) -> None:
        async with TaskQueue(db_path=str(tmp_path / "test.db")) as queue:
            task_id = queue.enqueue(type="test", payload={})
            assert isinstance(task_id, TaskId)
            assert queue._sync_conn is not None
        assert queue._sync_conn is None
