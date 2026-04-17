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

    def test_fresh_db_has_all_new_columns(self, tmp_path: Path) -> None:
        conn = init_sync_database(str(tmp_path / "test.db"))
        cols = [row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()]
        expected_new = {
            "cancelled_at",
            "error_code",
            "error_message",
            "input_prompt",
            "input_schema",
            "input_response",
            "input_seq",
        }
        assert expected_new.issubset(set(cols))
        conn.close()

    def test_task_progress_table_exists(self, tmp_path: Path) -> None:
        conn = init_sync_database(str(tmp_path / "test.db"))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='task_progress'"
        ).fetchall()
        assert len(tables) == 1
        conn.close()

    def test_migrations_idempotent_on_legacy_db(self, tmp_path: Path) -> None:
        """Simulate an old database without the new columns; init should upgrade it."""
        path = str(tmp_path / "legacy.db")
        legacy = sqlite3.connect(path)
        legacy.executescript(
            """
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                result TEXT,
                priority INTEGER NOT NULL DEFAULT 0,
                ttl_seconds INTEGER NOT NULL DEFAULT 300,
                created_at TEXT NOT NULL,
                claimed_at TEXT,
                completed_at TEXT,
                capabilities_required TEXT NOT NULL DEFAULT '[]',
                input_tokens INTEGER,
                output_tokens INTEGER
            );
            CREATE TABLE heartbeats (
                worker_id TEXT PRIMARY KEY,
                capabilities TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );
            """
        )
        legacy.commit()
        legacy.close()

        upgraded = init_sync_database(path)
        cols = [row["name"] for row in upgraded.execute("PRAGMA table_info(tasks)").fetchall()]
        assert "cancelled_at" in cols
        assert "error_code" in cols
        assert "input_seq" in cols
        upgraded.close()
