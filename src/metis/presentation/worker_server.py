"""Metis worker MCP server — exposes poll() and deliver() tools to a dispatcher agent.

NOT responsible for:
- Task lifecycle logic (see domain entities)
- Queue coordination (see application use cases)
- Database management (see infrastructure layer)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from metis.application.deliver_result import DeliverResultInput, DeliverResultUseCase
from metis.application.poll_task import PollTaskInput, PollTaskUseCase
from metis.infrastructure.database import init_async_database
from metis.infrastructure.sqlite_heartbeat_store import SqliteHeartbeatStore
from metis.infrastructure.sqlite_task_store import SqliteTaskStore

_poll_use_case: PollTaskUseCase | None = None
_deliver_use_case: DeliverResultUseCase | None = None


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Initialize database and wire use cases on startup."""
    global _poll_use_case, _deliver_use_case

    db_path = os.environ.get("METIS_DB_PATH", "~/.metis/metis.db")
    conn = await init_async_database(db_path)

    task_store = SqliteTaskStore(conn)
    hb_store = SqliteHeartbeatStore(conn)

    _poll_use_case = PollTaskUseCase(task_store=task_store, heartbeat_store=hb_store)
    _deliver_use_case = DeliverResultUseCase(task_store=task_store)

    try:
        yield
    finally:
        await conn.close()


mcp = FastMCP("metis-worker", lifespan=lifespan)


@mcp.tool()
async def poll(
    worker_id: str = "default",
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    """Poll for the next available task.

    Returns {"s": "e"} if no task is available (minimal tokens).
    Returns {"s": "t", "id": ..., "type": ..., "payload": ...} if a task is claimed.
    """
    assert _poll_use_case is not None, "Server not initialized"

    result = await _poll_use_case.execute(
        PollTaskInput(
            worker_id=worker_id,
            capabilities=capabilities or [],
        )
    )

    if result.is_error:
        return {"s": "err", "message": result.error.message}

    task = result.value
    if task is None:
        return {"s": "e"}

    return {
        "s": "t",
        "id": task.id.value,
        "type": task.type,
        "payload": task.payload,
    }


@mcp.tool()
async def deliver(task_id: str, result: dict[str, Any]) -> dict[str, str]:
    """Deliver a completed result for a claimed task.

    Returns {"s": "ok"} on success.
    Returns {"s": "err", "message": ...} on failure.
    """
    assert _deliver_use_case is not None, "Server not initialized"

    deliver_result = await _deliver_use_case.execute(
        DeliverResultInput(task_id=task_id, result=result)
    )

    if deliver_result.is_error:
        return {"s": "err", "message": deliver_result.error.message}

    return {"s": "ok"}


if __name__ == "__main__":
    mcp.run()
