"""Infrastructure tests for SqliteHeartbeatStore — real SQLite I/O."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiosqlite

from metis.domain.entities import Heartbeat
from metis.domain.value_objects import WorkerId
from metis.infrastructure.sqlite_heartbeat_store import SqliteHeartbeatStore


class TestUpsertAndGet:
    async def test_should_round_trip_heartbeat(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteHeartbeatStore(db_conn)
        hb = Heartbeat(
            worker_id=WorkerId(value="w1"),
            capabilities=["browse-as-me", "file-access"],
            last_seen=datetime.now(UTC),
        )

        await store.upsert(hb)
        retrieved = await store.get(WorkerId(value="w1"))

        assert retrieved is not None
        assert retrieved.worker_id == hb.worker_id
        assert retrieved.capabilities == ["browse-as-me", "file-access"]

    async def test_should_update_existing_heartbeat(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteHeartbeatStore(db_conn)
        worker = WorkerId(value="w1")
        old_time = datetime.now(UTC) - timedelta(seconds=30)
        new_time = datetime.now(UTC)

        await store.upsert(Heartbeat(worker_id=worker, capabilities=[], last_seen=old_time))
        await store.upsert(Heartbeat(worker_id=worker, capabilities=["new"], last_seen=new_time))

        retrieved = await store.get(worker)
        assert retrieved is not None
        assert retrieved.capabilities == ["new"]
        assert retrieved.last_seen >= new_time - timedelta(seconds=1)

    async def test_should_return_none_for_missing_worker(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        store = SqliteHeartbeatStore(db_conn)
        result = await store.get(WorkerId(value="nonexistent"))
        assert result is None


class TestGetLatest:
    async def test_should_return_most_recent_heartbeat(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteHeartbeatStore(db_conn)
        old = Heartbeat(
            worker_id=WorkerId(value="w1"),
            capabilities=[],
            last_seen=datetime.now(UTC) - timedelta(seconds=60),
        )
        recent = Heartbeat(
            worker_id=WorkerId(value="w2"),
            capabilities=["browse-as-me"],
            last_seen=datetime.now(UTC),
        )

        await store.upsert(old)
        await store.upsert(recent)

        latest = await store.get_latest()
        assert latest is not None
        assert latest.worker_id == recent.worker_id

    async def test_should_return_none_when_no_heartbeats(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        store = SqliteHeartbeatStore(db_conn)
        result = await store.get_latest()
        assert result is None


class TestRemove:
    async def test_should_delete_heartbeat(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteHeartbeatStore(db_conn)
        worker = WorkerId(value="w1")
        await store.upsert(
            Heartbeat(worker_id=worker, capabilities=[], last_seen=datetime.now(UTC))
        )

        await store.remove(worker)

        result = await store.get(worker)
        assert result is None
