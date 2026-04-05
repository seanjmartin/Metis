"""Simulated dispatcher — polls the task queue and auto-completes tasks.

Demonstrates cross-process coordination without requiring an actual LLM agent.
Connects directly to the SQLite database (same as metis-worker would use).

Usage:
    python examples/simulated/dispatcher.py --db ~/.metis/metis.db

For each task claimed, generates a canned result based on task type.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from datetime import UTC, datetime

from metis.application.deliver_result import DeliverResultInput, DeliverResultUseCase
from metis.application.poll_task import PollTaskInput, PollTaskUseCase
from metis.domain.entities import Heartbeat
from metis.domain.value_objects import WorkerId
from metis.infrastructure.database import init_async_database
from metis.infrastructure.sqlite_heartbeat_store import SqliteHeartbeatStore
from metis.infrastructure.sqlite_task_store import SqliteTaskStore

WORKER_ID = "simulated-dispatcher"
POLL_INTERVAL = 2.0

CANNED_RESULTS: dict[str, dict] = {
    "classify": {"vault": "personal", "confidence": 0.9, "reasoning": "simulated"},
    "validate": {"safe": True, "risks": [], "sanitized": "content unchanged"},
    "summarize": {"summary": "This is a simulated summary of the vault contents."},
    "browser_extract": {"output": "| Symbol | Price |\n|--------|-------|\n| AAPL | 150 |"},
    "conflict_resolution": {"resolution": "local", "merged_content": None, "reasoning": "simulated"},
}


def get_canned_result(task_type: str) -> dict:
    """Return a canned result for known task types, or a generic one."""
    return CANNED_RESULTS.get(task_type, {"status": "completed", "type": task_type})


async def run_dispatcher(db_path: str) -> None:
    conn = await init_async_database(db_path)
    task_store = SqliteTaskStore(conn)
    hb_store = SqliteHeartbeatStore(conn)

    poll_uc = PollTaskUseCase(task_store=task_store, heartbeat_store=hb_store)
    deliver_uc = DeliverResultUseCase(task_store=task_store)

    print(f"Simulated dispatcher started (db: {db_path})")
    print(f"Worker ID: {WORKER_ID}")
    print(f"Poll interval: {POLL_INTERVAL}s")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            result = await poll_uc.execute(
                PollTaskInput(worker_id=WORKER_ID, capabilities=list(CANNED_RESULTS.keys()))
            )

            if result.is_ok and result.value is not None:
                task = result.value
                print(f"[{datetime.now(UTC):%H:%M:%S}] Claimed task {task.id} (type={task.type})")

                canned = get_canned_result(task.type)
                await deliver_uc.execute(
                    DeliverResultInput(task_id=task.id.value, result=canned)
                )
                print(f"[{datetime.now(UTC):%H:%M:%S}] Delivered result for {task.id}")
            else:
                print(f"[{datetime.now(UTC):%H:%M:%S}] No tasks. Polling...")

            await asyncio.sleep(POLL_INTERVAL)
    except asyncio.CancelledError:
        pass
    finally:
        await conn.close()
        print("\nDispatcher stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulated Metis dispatcher")
    parser.add_argument(
        "--db",
        default="~/.metis/metis.db",
        help="Path to the Metis SQLite database (default: ~/.metis/metis.db)",
    )
    args = parser.parse_args()

    asyncio.run(run_dispatcher(args.db))


if __name__ == "__main__":
    main()
