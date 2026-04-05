"""Embeddable metis-worker tools for any FastMCP server.

Register poll/deliver/probe tools on an existing FastMCP instance,
so any MCP server can host dispatcher tools directly
without needing a separate metis-worker process.

Usage:
    from metis.presentation.worker_tools import register_worker_tools

    mcp = FastMCP("my-server")
    handle = register_worker_tools(mcp, db_path="~/.my-server/metis.db")

NOT responsible for:
- Task lifecycle logic (see domain entities)
- Queue coordination (see application use cases)
- Standalone server configuration (see worker_server.py)
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP

from metis.application.deliver_result import DeliverResultInput, DeliverResultUseCase
from metis.application.poll_task import PollTaskInput, PollTaskUseCase
from metis.infrastructure.database import init_async_database
from metis.infrastructure.sqlite_heartbeat_store import SqliteHeartbeatStore
from metis.infrastructure.sqlite_task_store import SqliteTaskStore


@dataclass
class WorkerToolsHandle:
    """References to registered tool functions and lifespan.

    NOT responsible for:
    - Tool implementation (see the closures created by register_worker_tools)
    - Server lifecycle (see the host FastMCP server)
    """

    poll: Callable
    deliver: Callable
    probe: Callable
    lifespan: Callable


def register_worker_tools(
    mcp: FastMCP,
    db_path: str = "~/.metis/metis.db",
    poll_timeout: int = 0,
) -> WorkerToolsHandle:
    """Register poll/deliver/probe tools on an existing FastMCP instance.

    NOT responsible for:
    - Creating the FastMCP server (caller does that)
    - Starting/stopping the server (caller does that)
    - Enqueue-side tools (see trigger_tools.py)
    """
    state: dict[str, Any] = {
        "poll_uc": None,
        "deliver_uc": None,
        "conn": None,
    }

    @asynccontextmanager
    async def lifespan(server: FastMCP):
        conn = await init_async_database(db_path)
        state["conn"] = conn

        task_store = SqliteTaskStore(conn)
        hb_store = SqliteHeartbeatStore(conn)

        state["poll_uc"] = PollTaskUseCase(
            task_store=task_store, heartbeat_store=hb_store
        )
        state["deliver_uc"] = DeliverResultUseCase(task_store=task_store)

        try:
            yield
        finally:
            if state["conn"] is not None:
                await state["conn"].close()
                state["conn"] = None

    @mcp.tool()
    async def poll(
        worker_id: str = "default",
        capabilities: list[str] | None = None,
        timeout: int = -1,
    ) -> dict[str, Any]:
        """Poll for the next available task.

        With timeout=0, returns immediately (classic poll).
        With timeout>0, blocks server-side until a task appears or timeout
        expires — minimizes idle token cost for LLM dispatchers.
        Default timeout is read from METIS_POLL_TIMEOUT env var (0 if unset).

        Returns {"s": "e"} if no task is available (minimal tokens).
        Returns {"s": "t", "id": ..., "type": ..., "payload": ...} if a task is claimed.
        """
        assert state["poll_uc"] is not None, "Worker tools not initialized"

        effective_timeout = timeout if timeout >= 0 else poll_timeout

        result = await state["poll_uc"].execute(
            PollTaskInput(
                worker_id=worker_id,
                capabilities=capabilities or [],
                timeout_seconds=effective_timeout,
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
        assert state["deliver_uc"] is not None, "Worker tools not initialized"

        deliver_result = await state["deliver_uc"].execute(
            DeliverResultInput(task_id=task_id, result=result)
        )

        if deliver_result.is_error:
            return {"s": "err", "message": deliver_result.error.message}

        return {"s": "ok"}

    @mcp.tool()
    async def probe(duration: int = 30) -> dict[str, object]:
        """Sleep for the specified duration to test MCP client timeout limits.

        Call with increasing durations (e.g. 10, 30, 50, 60, 70) to find the
        client's limit. When a call fails or times out, the limit is between
        the last successful duration and the failed one.

        Recommended METIS_POLL_TIMEOUT = last successful duration - 5 seconds.
        """
        start = time.monotonic()
        await asyncio.sleep(duration)
        elapsed = time.monotonic() - start
        return {
            "completed": True,
            "requested_seconds": duration,
            "actual_seconds": round(elapsed, 1),
        }

    return WorkerToolsHandle(
        poll=poll,
        deliver=deliver,
        probe=probe,
        lifespan=lifespan,
    )
