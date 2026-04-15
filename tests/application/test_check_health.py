"""Tests for CheckHealthUseCase."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiosqlite

from metis.application.check_health import CheckHealthUseCase
from metis.domain.entities import Heartbeat
from metis.domain.value_objects import WorkerId
from metis.infrastructure.sqlite_heartbeat_store import SqliteHeartbeatStore


class TestCheckHealth:
    async def test_should_return_false_when_no_heartbeats(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        hb_store = SqliteHeartbeatStore(db_conn)
        use_case = CheckHealthUseCase(heartbeat_store=hb_store)

        result = await use_case.execute()
        assert result.is_ok
        assert result.value is False

    async def test_should_return_true_when_worker_alive(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        hb_store = SqliteHeartbeatStore(db_conn)
        use_case = CheckHealthUseCase(heartbeat_store=hb_store)

        await hb_store.upsert(
            Heartbeat(
                worker_id=WorkerId(value="w1"),
                capabilities=[],
                last_seen=datetime.now(UTC),
            )
        )

        result = await use_case.execute()
        assert result.is_ok
        assert result.value is True

    async def test_should_return_false_when_worker_stale(
        self, db_conn: aiosqlite.Connection
    ) -> None:
        hb_store = SqliteHeartbeatStore(db_conn)
        use_case = CheckHealthUseCase(heartbeat_store=hb_store)

        await hb_store.upsert(
            Heartbeat(
                worker_id=WorkerId(value="w1"),
                capabilities=[],
                last_seen=datetime.now(UTC) - timedelta(seconds=120),
            )
        )

        result = await use_case.execute(timeout_seconds=60)
        assert result.is_ok
        assert result.value is False

    async def test_should_respect_custom_timeout(self, db_conn: aiosqlite.Connection) -> None:
        hb_store = SqliteHeartbeatStore(db_conn)
        use_case = CheckHealthUseCase(heartbeat_store=hb_store)

        await hb_store.upsert(
            Heartbeat(
                worker_id=WorkerId(value="w1"),
                capabilities=[],
                last_seen=datetime.now(UTC) - timedelta(seconds=30),
            )
        )

        # With 60s timeout, should be alive
        result = await use_case.execute(timeout_seconds=60)
        assert result.value is True

        # With 10s timeout, should be dead
        result = await use_case.execute(timeout_seconds=10)
        assert result.value is False
