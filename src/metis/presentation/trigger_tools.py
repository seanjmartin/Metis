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
        ttl_seconds: int = 300,
        capabilities_required: list[str] | None = None,
    ) -> dict[str, Any]:
        """Enqueue a reasoning task for the background dispatcher.

        Returns {"task_id": "<uuid>"} on success.
        Use capabilities_required to restrict which workers can claim this task.
        """
        if state["queue"] is None:
            return {"status": "error", "message": "Trigger tools not initialized"}

        task_id = state["queue"].enqueue(
            type=type,
            payload=payload,
            priority=priority,
            ttl_seconds=ttl_seconds,
            capabilities_required=capabilities_required,
        )

        return {"task_id": task_id.value}

    @mcp.tool()
    async def get_result(task_id: str, timeout: float = 30) -> dict[str, Any]:
        """Wait for a task's result.

        Returns {"status": "complete", "result": {...}, "input_tokens": N, "output_tokens": N}.
        Returns {"status": "timeout"} if the timeout expires.
        Returns {"status": "error", "message": "..."} on failure.
        """
        if state["queue"] is None:
            return {"status": "error", "message": "Trigger tools not initialized"}

        from metis.domain.value_objects import TaskId

        try:
            tid = TaskId(value=task_id)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        try:
            result = await state["queue"].wait_for_result(tid, timeout=timeout)
        except (RuntimeError, ValueError) as e:
            return {"status": "error", "message": str(e)}

        if result is None:
            return {"status": "timeout"}

        input_tokens, output_tokens = state["queue"].get_task_tokens(tid)
        response: dict[str, Any] = {"status": "complete", "result": result}
        if input_tokens is not None:
            response["input_tokens"] = input_tokens
        if output_tokens is not None:
            response["output_tokens"] = output_tokens
        return response

    @mcp.tool()
    async def check_health(timeout_seconds: int = 60) -> dict[str, Any]:
        """Check if the dispatcher worker is alive.

        Returns {"worker_alive": true/false}.
        """
        if state["queue"] is None:
            return {"status": "error", "message": "Trigger tools not initialized"}

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
