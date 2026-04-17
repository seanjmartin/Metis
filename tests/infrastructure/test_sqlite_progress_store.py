"""Tests for SqliteProgressStore — append-only progress log."""

from __future__ import annotations

import aiosqlite

from metis.domain.value_objects import TaskId
from metis.infrastructure.sqlite_progress_store import SqliteProgressStore


class TestAppend:
    async def test_returns_monotonic_seq(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteProgressStore(db_conn)
        tid = TaskId.generate()

        s1 = await store.append(tid, progress=0.1, message="a")
        s2 = await store.append(tid, progress=0.5, message="b")
        s3 = await store.append(tid, progress=0.9, total=1.0, message="c")

        assert s1 == 1
        assert s2 == 2
        assert s3 == 3

    async def test_seq_is_per_task(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteProgressStore(db_conn)
        t1 = TaskId.generate()
        t2 = TaskId.generate()

        await store.append(t1, progress=0.1)
        await store.append(t1, progress=0.2)
        s = await store.append(t2, progress=0.5)

        assert s == 1  # first entry for t2


class TestTailSince:
    async def test_returns_empty_when_no_updates(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteProgressStore(db_conn)
        updates = await store.tail_since(TaskId.generate(), last_seq=0)
        assert updates == []

    async def test_returns_only_after_last_seq(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteProgressStore(db_conn)
        tid = TaskId.generate()

        await store.append(tid, progress=0.1, message="a")
        await store.append(tid, progress=0.5, message="b")
        await store.append(tid, progress=0.9, message="c")

        new_after_one = await store.tail_since(tid, last_seq=1)
        assert [u.message for u in new_after_one] == ["b", "c"]

        new_after_all = await store.tail_since(tid, last_seq=3)
        assert new_after_all == []

    async def test_preserves_order(self, db_conn: aiosqlite.Connection) -> None:
        store = SqliteProgressStore(db_conn)
        tid = TaskId.generate()

        for i in range(5):
            await store.append(tid, progress=float(i) / 10, message=f"m{i}")

        updates = await store.tail_since(tid, last_seq=0)
        assert [u.seq for u in updates] == [1, 2, 3, 4, 5]
        assert [u.message for u in updates] == ["m0", "m1", "m2", "m3", "m4"]
