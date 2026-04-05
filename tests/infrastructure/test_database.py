"""Tests for database initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from metis.infrastructure.database import init_sync_database


class TestInitSyncDatabase:
    def test_should_create_tables(self, tmp_path: Path) -> None:
        conn = init_sync_database(str(tmp_path / "test.db"))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [row["name"] for row in tables]
        assert "tasks" in table_names
        assert "heartbeats" in table_names
        conn.close()

    def test_should_enable_wal_mode(self, tmp_path: Path) -> None:
        conn = init_sync_database(str(tmp_path / "test.db"))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_should_create_parent_directories(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "nested" / "deep" / "test.db")
        conn = init_sync_database(db_path)
        assert Path(db_path).exists()
        conn.close()

    def test_should_set_row_factory(self, tmp_path: Path) -> None:
        conn = init_sync_database(str(tmp_path / "test.db"))
        assert conn.row_factory is sqlite3.Row
        conn.close()
