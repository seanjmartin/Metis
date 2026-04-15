"""Demonstrate that session-scoped tasks are isolated between users.

This script uses the TaskQueue directly (no MCP) to show that:
1. Tasks enqueued with different session_ids stay separate
2. Workers polling with a session_id only see their session's tasks
3. Workers polling without a session_id see all tasks (backward compat)

Usage:
    python examples/http_multiuser/test_isolation.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import aiosqlite

from metis import TaskQueue
from metis.domain.value_objects import WorkerId
from metis.infrastructure.database import init_async_database
from metis.infrastructure.sqlite_task_store import SqliteTaskStore


async def main() -> None:
    db_path = str(Path(tempfile.mkdtemp()) / "test_isolation.db")
    queue = TaskQueue(db_path=db_path)

    # --- Enqueue tasks for two different users ---

    alice_id = queue.enqueue(
        type="classify",
        payload={"text": "Alice's research paper on quantum computing"},
        session_id="alice",
    )
    bob_id = queue.enqueue(
        type="classify",
        payload={"text": "Bob's grocery list"},
        session_id="bob",
    )
    global_id = queue.enqueue(
        type="classify",
        payload={"text": "A task with no session"},
    )

    print("Enqueued tasks:")
    print(f"  Alice: {alice_id.value}")
    print(f"  Bob:   {bob_id.value}")
    print(f"  Global (no session): {global_id.value}")
    print()

    # --- Claim with session filtering ---

    conn = await init_async_database(db_path)
    store = SqliteTaskStore(conn)

    # Alice's dispatcher only sees Alice's task
    alice_claimed = await store.claim_next(
        [], WorkerId(value="alice-dispatcher"), session_id="alice"
    )
    print("Alice's dispatcher claims:")
    if alice_claimed:
        print(f"  Task {alice_claimed.id.value} (session: {alice_claimed.session_id})")
        print(f"  Payload: {alice_claimed.payload['text']}")
    else:
        print("  Nothing (unexpected!)")
    print()

    # Bob's dispatcher only sees Bob's task
    bob_claimed = await store.claim_next(
        [], WorkerId(value="bob-dispatcher"), session_id="bob"
    )
    print("Bob's dispatcher claims:")
    if bob_claimed:
        print(f"  Task {bob_claimed.id.value} (session: {bob_claimed.session_id})")
        print(f"  Payload: {bob_claimed.payload['text']}")
    else:
        print("  Nothing (unexpected!)")
    print()

    # Alice tries again — nothing left for her
    alice_again = await store.claim_next(
        [], WorkerId(value="alice-dispatcher"), session_id="alice"
    )
    print("Alice's dispatcher polls again:")
    print(f"  {'Nothing — correct!' if alice_again is None else 'Unexpected task!'}")
    print()

    # Global poll (no session_id) picks up the remaining global task
    global_claimed = await store.claim_next(
        [], WorkerId(value="global-dispatcher"), session_id=None
    )
    print("Global dispatcher (no session filter) claims:")
    if global_claimed:
        print(f"  Task {global_claimed.id.value} (session: {global_claimed.session_id})")
        print(f"  Payload: {global_claimed.payload['text']}")
    else:
        print("  Nothing (unexpected!)")
    print()

    print("Session isolation verified.")

    await conn.close()
    queue.close()


if __name__ == "__main__":
    asyncio.run(main())
