"""Infrastructure tests for SqliteTaskStore — real SQLite I/O."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiosqlite

from metis.domain.entities import Task
from metis.domain.value_objects import TaskId, TaskPriority, TaskStatus, WorkerId
from metis.infrastructure.sqlite_task_store import SqliteTaskStore


def _make_task(
    *,
    priority: int = 0,
    ttl_seconds: int = 300,
    created_at: datetime | None = None,
    task_type: str = "test",
) -> Task:
    return Task(
        id=TaskId.generate(),
        type=task_type,
        payload={"key": "value"},
        priority=TaskPriority(value=priority),
        ttl_seconds=ttl_seconds,
        created_at=created_at or datetime.now(UTC),
    )


class TestInsertAndGet:
    async def test_should_round_trip_task(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        task = _make_task()

        await store.insert(task)
        retrieved = await store.get(task.id)

        assert retrieved is not None
        assert retrieved.id == task.id
        assert retrieved.type == task.type
        assert retrieved.payload == task.payload
        assert retrieved.status == TaskStatus.PENDING
        assert retrieved.priority == task.priority

    async def test_should_return_none_for_missing_task(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        result = await store.get(TaskId.generate())
        assert result is None


class TestClaimNext:
    async def test_should_claim_highest_priority_first(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        low = _make_task(priority=0)
        high = _make_task(priority=10)

        await store.insert(low)
        await store.insert(high)

        claimed = await store.claim_next([], WorkerId(value="w1"))
        assert claimed is not None
        assert claimed.id == high.id
        assert claimed.status == TaskStatus.CLAIMED

    async def test_should_claim_oldest_at_same_priority(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        store = SqliteTaskStore(db_conn)
        older = _make_task(created_at=datetime.now(UTC) - timedelta(seconds=10))
        newer = _make_task()

        await store.insert(older)
        await store.insert(newer)

        claimed = await store.claim_next([], WorkerId(value="w1"))
        assert claimed is not None
        assert claimed.id == older.id

    async def test_should_return_none_when_no_pending_tasks(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        store = SqliteTaskStore(db_conn)
        result = await store.claim_next([], WorkerId(value="w1"))
        assert result is None

    async def test_should_not_double_claim(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        task = _make_task()
        await store.insert(task)

        first = await store.claim_next([], WorkerId(value="w1"))
        second = await store.claim_next([], WorkerId(value="w2"))

        assert first is not None
        assert second is None


class TestUpdate:
    async def test_should_persist_status_and_result(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        task = _make_task()
        await store.insert(task)

        task.claim(WorkerId(value="w1"))
        task.complete({"answer": 42})
        await store.update(task)

        retrieved = await store.get(task.id)
        assert retrieved is not None
        assert retrieved.status == TaskStatus.COMPLETE
        assert retrieved.result == {"answer": 42}
        assert retrieved.completed_at is not None


class TestMarkConsumed:
    async def test_should_set_status_to_consumed(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        task = _make_task()
        await store.insert(task)

        await store.mark_consumed(task.id)

        retrieved = await store.get(task.id)
        assert retrieved is not None
        assert retrieved.status == TaskStatus.CONSUMED


class TestExpireStale:
    async def test_should_expire_overdue_pending_tasks(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        stale = _make_task(
            ttl_seconds=10,
            created_at=datetime.now(UTC) - timedelta(seconds=20),
        )
        fresh = _make_task(ttl_seconds=300)

        await store.insert(stale)
        await store.insert(fresh)

        count = await store.expire_stale(datetime.now(UTC))
        assert count == 1

        stale_task = await store.get(stale.id)
        fresh_task = await store.get(fresh.id)
        assert stale_task is not None and stale_task.status == TaskStatus.EXPIRED
        assert fresh_task is not None and fresh_task.status == TaskStatus.PENDING

    async def test_should_expire_overdue_claimed_tasks(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        task = _make_task(
            ttl_seconds=10,
            created_at=datetime.now(UTC) - timedelta(seconds=20),
        )
        await store.insert(task)
        await store.claim_next([], WorkerId(value="w1"))

        count = await store.expire_stale(datetime.now(UTC))
        assert count == 1

    async def test_should_not_expire_completed_tasks(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        task = _make_task(
            ttl_seconds=10,
            created_at=datetime.now(UTC) - timedelta(seconds=20),
        )
        await store.insert(task)

        claimed = await store.claim_next([], WorkerId(value="w1"))
        assert claimed is not None
        claimed.complete({"done": True})
        await store.update(claimed)

        count = await store.expire_stale(datetime.now(UTC))
        assert count == 0


class TestCapabilityFiltering:
    async def test_should_claim_task_with_matching_capabilities(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        store = SqliteTaskStore(db_conn)
        task = _make_task()
        task.capabilities_required = ["browse-as-me"]
        await store.insert(task)

        claimed = await store.claim_next(["browse-as-me", "file-access"], WorkerId(value="w1"))
        assert claimed is not None
        assert claimed.id == task.id

    async def test_should_skip_task_without_matching_capabilities(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        store = SqliteTaskStore(db_conn)
        task = _make_task()
        task.capabilities_required = ["browse-as-me"]
        await store.insert(task)

        claimed = await store.claim_next(["file-access"], WorkerId(value="w1"))
        assert claimed is None

    async def test_should_claim_task_with_no_requirements(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        store = SqliteTaskStore(db_conn)
        task = _make_task()  # capabilities_required defaults to []
        await store.insert(task)

        claimed = await store.claim_next([], WorkerId(value="w1"))
        assert claimed is not None

    async def test_should_skip_restricted_and_claim_unrestricted(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        store = SqliteTaskStore(db_conn)
        restricted = _make_task(priority=10)
        restricted.capabilities_required = ["browse-as-me"]
        unrestricted = _make_task(priority=0)

        await store.insert(restricted)
        await store.insert(unrestricted)

        # Worker without browse-as-me should skip the higher-priority restricted task
        claimed = await store.claim_next([], WorkerId(value="w1"))
        assert claimed is not None
        assert claimed.id == unrestricted.id

    async def test_should_require_all_capabilities(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        task = _make_task()
        task.capabilities_required = ["browse-as-me", "file-access"]
        await store.insert(task)

        # Only one of two required — should not claim
        claimed = await store.claim_next(["browse-as-me"], WorkerId(value="w1"))
        assert claimed is None

        # Both required — should claim
        claimed = await store.claim_next(["browse-as-me", "file-access"], WorkerId(value="w1"))
        assert claimed is not None


class TestSessionFiltering:
    async def test_should_claim_task_with_matching_session_id(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        store = SqliteTaskStore(db_conn)
        alice_task = _make_task()
        alice_task.session_id = "alice"
        bob_task = _make_task()
        bob_task.session_id = "bob"

        await store.insert(alice_task)
        await store.insert(bob_task)

        claimed = await store.claim_next([], WorkerId(value="w1"), session_id="alice")
        assert claimed is not None
        assert claimed.id == alice_task.id
        assert claimed.session_id == "alice"

    async def test_should_not_claim_task_with_different_session_id(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        store = SqliteTaskStore(db_conn)
        task = _make_task()
        task.session_id = "alice"
        await store.insert(task)

        claimed = await store.claim_next([], WorkerId(value="w1"), session_id="bob")
        assert claimed is None

    async def test_should_claim_any_when_session_id_is_none(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        store = SqliteTaskStore(db_conn)
        task = _make_task()
        task.session_id = "alice"
        await store.insert(task)

        claimed = await store.claim_next([], WorkerId(value="w1"), session_id=None)
        assert claimed is not None
        assert claimed.id == task.id

    async def test_should_round_trip_session_id(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        task = _make_task()
        task.session_id = "user-a::session-3"
        await store.insert(task)

        retrieved = await store.get(task.id)
        assert retrieved is not None
        assert retrieved.session_id == "user-a::session-3"

    async def test_should_default_session_id_to_none(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        task = _make_task()
        await store.insert(task)

        retrieved = await store.get(task.id)
        assert retrieved is not None
        assert retrieved.session_id is None


class TestTokenTracking:
    async def test_should_round_trip_token_counts(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        task = _make_task()
        await store.insert(task)

        claimed = await store.claim_next([], WorkerId(value="w1"))
        assert claimed is not None
        claimed.complete({"answer": 42})
        claimed.input_tokens = 1500
        claimed.output_tokens = 500
        await store.update(claimed)

        retrieved = await store.get(task.id)
        assert retrieved is not None
        assert retrieved.input_tokens == 1500
        assert retrieved.output_tokens == 500

    async def test_should_default_tokens_to_none(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteTaskStore(db_conn)
        task = _make_task()
        await store.insert(task)

        retrieved = await store.get(task.id)
        assert retrieved is not None
        assert retrieved.input_tokens is None
        assert retrieved.output_tokens is None
