"""Metis trigger MCP server — exposes enqueue and result retrieval to the calling conversation.

This is the enqueue side of Metis. The main conversation (or any MCP client) uses
these tools to submit tasks and retrieve results. The dispatcher sub-agent uses
metis-worker (poll/deliver) on the other side of the same SQLite database.

NOT responsible for:
- Task execution (see dispatcher agent via metis-worker)
- Poll/deliver coordination (see worker_server.py)
- Task lifecycle logic (see domain entities)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from metis.infrastructure.task_queue_facade import TaskQueue

_queue: TaskQueue | None = None


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Initialize TaskQueue on startup."""
    global _queue

    db_path = os.environ.get("METIS_DB_PATH", "~/.metis/metis.db")
    _queue = TaskQueue(db_path=db_path)

    try:
        yield
    finally:
        if _queue is not None:
            _queue.close()
            _queue = None


mcp = FastMCP("metis-trigger", lifespan=lifespan)


@mcp.tool()
async def enqueue(
    type: str,
    payload: dict[str, Any],
    priority: int = 0,
    ttl_seconds: int = 120,
) -> dict[str, str]:
    """Enqueue a reasoning task for the background dispatcher.

    Returns {"task_id": "<uuid>"} on success.
    """
    assert _queue is not None, "Server not initialized"

    task_id = _queue.enqueue(
        type=type,
        payload=payload,
        priority=priority,
        ttl_seconds=ttl_seconds,
    )

    return {"task_id": task_id.value}


@mcp.tool()
async def get_result(task_id: str, timeout: float = 30) -> dict[str, Any]:
    """Wait for a task's result.

    Returns {"status": "complete", "result": {...}} if the task completes.
    Returns {"status": "timeout"} if the timeout expires.
    Returns {"status": "error", "message": "..."} on failure.
    """
    assert _queue is not None, "Server not initialized"

    from metis.domain.value_objects import TaskId

    try:
        result = await _queue.wait_for_result(TaskId(value=task_id), timeout=timeout)
    except (RuntimeError, ValueError) as e:
        return {"status": "error", "message": str(e)}

    if result is None:
        return {"status": "timeout"}

    return {"status": "complete", "result": result}


@mcp.tool()
async def check_health(timeout_seconds: int = 60) -> dict[str, bool]:
    """Check if the dispatcher worker is alive.

    Returns {"worker_alive": true/false}.
    """
    assert _queue is not None, "Server not initialized"

    return {"worker_alive": _queue.is_worker_alive(timeout_seconds=timeout_seconds)}


if __name__ == "__main__":
    mcp.run()
