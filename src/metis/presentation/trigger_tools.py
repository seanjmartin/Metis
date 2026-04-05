"""Embeddable metis-trigger tools for any FastMCP server.

Register enqueue/get_result/check_health tools on an existing FastMCP
instance, so the main conversation can submit tasks and retrieve results
without needing a separate metis-trigger process.

Usage:
    from metis.presentation.trigger_tools import register_trigger_tools

    mcp = FastMCP("my-server")
    handle = register_trigger_tools(mcp, db_path="~/.my-server/metis.db")

NOT responsible for:
- Task execution (see dispatcher agent via worker tools)
- Standalone server configuration (see trigger_server.py)
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP

from metis.infrastructure.task_queue_facade import TaskQueue


@dataclass
class TriggerToolsHandle:
    """References to registered tool functions and lifespan.

    NOT responsible for:
    - Tool implementation (see the closures created by register_trigger_tools)
    - Server lifecycle (see the host FastMCP server)
    """

    enqueue: Callable
    get_result: Callable
    check_health: Callable
    lifespan: Callable


def register_trigger_tools(
    mcp: FastMCP,
    db_path: str = "~/.metis/metis.db",
) -> TriggerToolsHandle:
    """Register enqueue/get_result/check_health tools on an existing FastMCP instance.

    NOT responsible for:
    - Creating the FastMCP server (caller does that)
    - Starting/stopping the server (caller does that)
    - Worker-side tools (see worker_tools.py)
    """
    state: dict[str, Any] = {"queue": None}

    @asynccontextmanager
    async def lifespan(server: FastMCP):
        state["queue"] = TaskQueue(db_path=db_path)
        try:
            yield
        finally:
            if state["queue"] is not None:
                state["queue"].close()
                state["queue"] = None

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
        assert state["queue"] is not None, "Trigger tools not initialized"

        task_id = state["queue"].enqueue(
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
        assert state["queue"] is not None, "Trigger tools not initialized"

        from metis.domain.value_objects import TaskId

        try:
            result = await state["queue"].wait_for_result(
                TaskId(value=task_id), timeout=timeout
            )
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
        assert state["queue"] is not None, "Trigger tools not initialized"

        return {
            "worker_alive": state["queue"].is_worker_alive(
                timeout_seconds=timeout_seconds
            )
        }

    return TriggerToolsHandle(
        enqueue=enqueue,
        get_result=get_result,
        check_health=check_health,
        lifespan=lifespan,
    )
