"""Embeddable metis-trigger tools for any FastMCP server.

Register enqueue/get_result/cancel/provide_input/check_health tools on an
existing FastMCP instance, so the main conversation can submit tasks,
retrieve results, and cancel in-flight work without needing a separate
metis-trigger process.

Response shapes align with the MCP async-tasks spec (2025-11-25):

    enqueue(...)     -> {"task": {"id": "...", "status": "working"}}
    get_result(...)  -> {"task": {"id": "...", "status": <spec-status>},
                         "result"?: {...}, "error"?: {...},
                         "metis"?: {"input_tokens": N, "output_tokens": N}}
    cancel(...)      -> {"task": {"id": "...", "status": "cancelled"}}

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

from mcp.server.fastmcp import Context, FastMCP

from metis.domain.errors import MetisException
from metis.domain.spec_mapping import internal_to_spec_status
from metis.domain.value_objects import TaskId, TaskStatus
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
    cancel: Callable
    provide_input: Callable
    check_health: Callable
    lifespan: Callable


def _task_envelope(task_id: str, spec_status: str) -> dict[str, Any]:
    """Build a spec-compliant task reference envelope."""
    return {"task": {"id": task_id, "status": spec_status}}


def register_trigger_tools(
    mcp: FastMCP,
    db_path: str = "~/.metis/metis.db",
    session_id: str | Callable[[], str | None] | None = None,
) -> TriggerToolsHandle:
    """Register enqueue/get_result/cancel/provide_input/check_health tools.

    session_id can be a static string, a callable returning the current
    session ID (for HTTP servers with per-request context), or None for
    the global task pool.

    NOT responsible for:
    - Creating the FastMCP server (caller does that)
    - Starting/stopping the server (caller does that)
    - Worker-side tools (see worker_tools.py)
    """
    state: dict[str, Any] = {"queue": None, "lifespan_entered": False}

    @asynccontextmanager
    async def lifespan(server: FastMCP):
        state["queue"] = TaskQueue(db_path=db_path)
        state["lifespan_entered"] = True
        try:
            yield
        finally:
            if state["queue"] is not None:
                state["queue"].close()
                state["queue"] = None

    def _require_initialized() -> None:
        if not state["lifespan_entered"]:
            raise RuntimeError(
                "metis trigger tools not initialized: the handle.lifespan returned by "
                "register_trigger_tools() must be composed into your FastMCP server's "
                "lifespan. See examples/http_multiuser/ for the pattern."
            )

    @mcp.tool()
    async def enqueue(
        type: str,
        payload: dict[str, Any],
        priority: int = 0,
        ttl_seconds: int = 300,
        capabilities_required: list[str] | None = None,
    ) -> dict[str, Any]:
        """Enqueue a reasoning task for the background dispatcher.

        Returns the spec-compliant task envelope: {"task": {"id", "status": "working"}}.
        Use capabilities_required to restrict which workers can claim this task.
        """
        _require_initialized()

        resolved_sid = session_id() if callable(session_id) else session_id

        task_id = state["queue"].enqueue(
            type=type,
            payload=payload,
            priority=priority,
            ttl_seconds=ttl_seconds,
            capabilities_required=capabilities_required,
            session_id=resolved_sid,
        )

        return _task_envelope(task_id.value, "working")

    @mcp.tool()
    async def get_result(
        task_id: str,
        timeout: float = 30,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Wait for a task's result, using the MCP spec envelope.

        Shapes:
            Completed: {"task": {"id", "status": "completed"},
                        "result": {...}, "metis": {"input_tokens", "output_tokens"}}
            Failed:    {"task": {"id", "status": "failed"},
                        "error": {"code", "message"}}
            Cancelled: {"task": {"id", "status": "cancelled"}}
            Working:   {"task": {"id", "status": "working"}}  (timeout, client re-polls)

        Progress is forwarded to the originating client via ctx.report_progress()
        when a progress token is attached to the caller's request.

        If the task enters input_required, get_result transparently issues
        ctx.elicit() and forwards the response, then continues waiting.
        """
        _require_initialized()

        try:
            tid = TaskId(value=task_id)
        except ValueError as e:
            return {
                "task": {"id": task_id, "status": "failed"},
                "error": {"code": "INVALID_TASK_ID", "message": str(e)},
            }

        try:
            result = await state["queue"].wait_for_result(tid, timeout=timeout, ctx=ctx)
        except MetisException as e:
            spec_status = _spec_status_from_error_code(e.error.code)
            return {
                "task": {"id": task_id, "status": spec_status},
                "error": {"code": e.error.code, "message": e.error.message},
            }

        if result is None:
            # Look up the actual status — may be cancelled, not just working
            status = state["queue"].get_task_status(tid)
            if status is None:
                return _task_envelope(task_id, "working")
            return _task_envelope(task_id, internal_to_spec_status(status))

        input_tokens, output_tokens = state["queue"].get_task_tokens(tid)
        response: dict[str, Any] = {
            "task": {"id": task_id, "status": "completed"},
            "result": result,
        }
        metis_meta: dict[str, Any] = {}
        if input_tokens is not None:
            metis_meta["input_tokens"] = input_tokens
        if output_tokens is not None:
            metis_meta["output_tokens"] = output_tokens
        if metis_meta:
            response["metis"] = metis_meta
        return response

    @mcp.tool()
    async def cancel(task_id: str) -> dict[str, Any]:
        """Cancel a non-terminal task.

        Returns {"task": {"id", "status": "cancelled"}} on success.
        Returns an error envelope with code=TASK_ALREADY_TERMINAL (mapped to
        JSON-RPC -32602 semantics) when the task is already in a terminal state.
        """
        _require_initialized()

        try:
            tid = TaskId(value=task_id)
        except ValueError as e:
            return {
                "task": {"id": task_id, "status": "failed"},
                "error": {"code": "INVALID_TASK_ID", "message": str(e)},
            }

        try:
            await state["queue"].cancel(tid)
        except MetisException as e:
            spec_status = _spec_status_from_error_code(e.error.code)
            return {
                "task": {"id": task_id, "status": spec_status},
                "error": {
                    "code": e.error.code,
                    "message": e.error.message,
                    "json_rpc_code": -32602 if e.error.code == "TASK_ALREADY_TERMINAL" else None,
                },
            }

        return _task_envelope(task_id, "cancelled")

    @mcp.tool()
    async def provide_input(task_id: str, response: dict[str, Any]) -> dict[str, Any]:
        """Provide a response to a task that is awaiting user input.

        Normally callers don't invoke this directly — get_result handles
        INPUT_REQUIRED transparently via ctx.elicit(). This tool exists for
        debugging and for clients that prefer an explicit round-trip.
        """
        _require_initialized()

        try:
            tid = TaskId(value=task_id)
        except ValueError as e:
            return {
                "task": {"id": task_id, "status": "failed"},
                "error": {"code": "INVALID_TASK_ID", "message": str(e)},
            }

        try:
            await state["queue"].provide_input(tid, response)
        except MetisException as e:
            return {
                "task": {"id": task_id, "status": "failed"},
                "error": {"code": e.error.code, "message": e.error.message},
            }

        return _task_envelope(task_id, "working")

    @mcp.tool()
    async def check_health(timeout_seconds: int = 60) -> dict[str, Any]:
        """Check if the dispatcher worker is alive.

        Returns {"worker_alive": true|false}.
        """
        _require_initialized()

        return {"worker_alive": state["queue"].is_worker_alive(timeout_seconds=timeout_seconds)}

    return TriggerToolsHandle(
        enqueue=enqueue,
        get_result=get_result,
        cancel=cancel,
        provide_input=provide_input,
        check_health=check_health,
        lifespan=lifespan,
    )


def _spec_status_from_error_code(code: str) -> str:
    """Map a MetisError code to the spec task status to report alongside the error."""
    if code == "TASK_CANCELLED":
        return "cancelled"
    if code == "TASK_EXPIRED" or code == "TASK_FAILED":
        return "failed"
    if code == "TASK_NOT_FOUND":
        return "failed"
    if code == "TASK_ALREADY_TERMINAL":
        # Caller operated on a terminal task; surface the current spec status we can
        # derive from the code's semantics — default to failed if unclear.
        return "failed"
    return "failed"


# Exported for reuse by W4/W5 integration and by tests that need to verify
# status mapping from within the handler's response shape.
def _status_for_working_task(status: TaskStatus) -> str:
    return internal_to_spec_status(status)
