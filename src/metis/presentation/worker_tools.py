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
from metis.application.report_progress import ReportProgressInput, ReportProgressUseCase
from metis.domain.spec_mapping import internal_to_spec_status
from metis.domain.value_objects import TaskId
from metis.infrastructure.database import init_async_database
from metis.infrastructure.sqlite_heartbeat_store import SqliteHeartbeatStore
from metis.infrastructure.sqlite_progress_store import SqliteProgressStore
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
    report_progress: Callable
    check_cancelled: Callable
    request_input: Callable
    await_input_response: Callable
    request_sampling: Callable
    await_sampling_response: Callable
    lifespan: Callable


def register_worker_tools(
    mcp: FastMCP,
    db_path: str = "~/.metis/metis.db",
    poll_timeout: int = 0,
    session_id: str | Callable[[], str | None] | None = None,
) -> WorkerToolsHandle:
    """Register poll/deliver/probe tools on an existing FastMCP instance.

    session_id can be a static string, a callable returning the current
    session ID (for HTTP servers with per-request context), or None for
    the global task pool.

    NOT responsible for:
    - Creating the FastMCP server (caller does that)
    - Starting/stopping the server (caller does that)
    - Enqueue-side tools (see trigger_tools.py)
    """
    state: dict[str, Any] = {
        "poll_uc": None,
        "deliver_uc": None,
        "progress_uc": None,
        "task_store": None,
        "conn": None,
        "lifespan_entered": False,
    }

    @asynccontextmanager
    async def lifespan(server: FastMCP):
        conn = await init_async_database(db_path)
        state["conn"] = conn

        task_store = SqliteTaskStore(conn)
        hb_store = SqliteHeartbeatStore(conn)
        progress_store = SqliteProgressStore(conn)

        state["task_store"] = task_store
        state["poll_uc"] = PollTaskUseCase(task_store=task_store, heartbeat_store=hb_store)
        state["deliver_uc"] = DeliverResultUseCase(task_store=task_store)
        state["progress_uc"] = ReportProgressUseCase(
            task_store=task_store, progress_store=progress_store
        )
        state["lifespan_entered"] = True

        try:
            yield
        finally:
            if state["conn"] is not None:
                await state["conn"].close()
                state["conn"] = None

    def _require_initialized() -> None:
        if not state["lifespan_entered"]:
            raise RuntimeError(
                "metis worker tools not initialized: the handle.lifespan returned by "
                "register_worker_tools() must be composed into your FastMCP server's "
                "lifespan. See examples/http_multiuser/ for the pattern."
            )

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
        _require_initialized()

        effective_timeout = timeout if timeout >= 0 else poll_timeout
        resolved_sid = session_id() if callable(session_id) else session_id

        result = await state["poll_uc"].execute(
            PollTaskInput(
                worker_id=worker_id,
                capabilities=capabilities or [],
                timeout_seconds=effective_timeout,
                session_id=resolved_sid,
            )
        )

        if result.is_error:
            return {"s": "err", "message": result.error.message}

        task = result.value
        if task is None:
            return {"s": "e"}

        response = {
            "s": "t",
            "id": task.id.value,
            "type": task.type,
            "payload": task.payload,
        }
        if task.session_id is not None:
            response["sid"] = task.session_id
        return response

    @mcp.tool()
    async def deliver(
        task_id: str,
        result: dict[str, Any],
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> dict[str, str]:
        """Deliver a completed result for a claimed task.

        Optionally include input_tokens and output_tokens for cost tracking.

        Returns {"s": "ok"} on success.
        Returns {"s": "err", "message": ...} on failure.
        """
        _require_initialized()

        deliver_result = await state["deliver_uc"].execute(
            DeliverResultInput(
                task_id=task_id,
                result=result,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
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

    @mcp.tool()
    async def report_progress(
        task_id: str,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        """Report a progress update for a claimed task.

        The originating trigger-side tool forwards this via the client's
        progressToken when available, so end users see motion.

        Returns {"s": "ok", "seq": N} on success.
        Returns {"s": "err", "message": ...} if the task is terminal or missing.
        """
        _require_initialized()

        result = await state["progress_uc"].execute(
            ReportProgressInput(
                task_id=task_id,
                progress=progress,
                total=total,
                message=message,
            )
        )
        if result.is_error:
            return {"s": "err", "code": result.error.code, "message": result.error.message}
        return {"s": "ok", "seq": result.value}

    @mcp.tool()
    async def check_cancelled(task_id: str) -> dict[str, Any]:
        """Check whether the client has requested cancellation of this task.

        Long-running dispatchers should call this between sub-steps so they
        can bail out early rather than burning work the client has abandoned.

        Returns {"cancelled": true|false, "status": <spec-status>}.
        """
        _require_initialized()

        task = await state["task_store"].get(TaskId(value=task_id))
        if task is None:
            return {"cancelled": False, "status": "failed", "error": "TASK_NOT_FOUND"}
        return {
            "cancelled": task.status.value == "cancelled",
            "status": internal_to_spec_status(task.status),
        }

    @mcp.tool()
    async def request_input(
        task_id: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Pause the task and ask the originating client for user input.

        Transitions the task from CLAIMED to INPUT_REQUIRED; the trigger-side
        wait loop observes this, calls ctx.elicit(), and writes the response
        back via provide_input. Call await_input_response(task_id) to block
        for the reply.

        Returns {"s": "ok", "seq": N} where N is the new input_seq.
        """
        _require_initialized()

        store = state["task_store"]
        task = await store.get(TaskId(value=task_id))
        if task is None:
            return {"s": "err", "code": "TASK_NOT_FOUND"}
        if task.status.is_terminal:
            return {"s": "err", "code": "TASK_ALREADY_TERMINAL", "status": task.status.value}
        try:
            task.request_input(prompt, schema)
        except ValueError as e:
            return {"s": "err", "code": "INVALID_TRANSITION", "message": str(e)}
        await store.update(task)
        return {"s": "ok", "seq": task.input_seq}

    @mcp.tool()
    async def await_input_response(
        task_id: str,
        seq: int,
        timeout: float = 55.0,
    ) -> dict[str, Any]:
        """Long-poll for the trigger side to write back an input response.

        Pass seq = the seq returned by request_input. When the trigger side
        calls provide_input, the task transitions back to CLAIMED and its
        input_response field is populated — this tool then returns it.

        Returns {"s": "resp", "response": {...}} when a response arrives.
        Returns {"s": "timeout"} if the client didn't respond in time.
        Returns {"s": "cancelled"} if the task was cancelled while waiting.
        """
        _require_initialized()

        store = state["task_store"]
        tid = TaskId(value=task_id)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = await store.get(tid)
            if task is None:
                return {"s": "err", "code": "TASK_NOT_FOUND"}
            if task.status.value == "cancelled":
                return {"s": "cancelled"}
            if task.status.is_terminal:
                return {"s": "err", "code": "TASK_ALREADY_TERMINAL", "status": task.status.value}
            # Input was provided and task transitioned back to CLAIMED
            if task.input_seq >= seq and task.input_response is not None:
                return {"s": "resp", "response": task.input_response}
            await asyncio.sleep(0.25)
        return {"s": "timeout"}

    @mcp.tool()
    async def request_sampling(
        task_id: str,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Ask the originating client's LLM to generate a completion.

        Uses the MCP sampling primitive via ctx.session.create_message on the
        trigger side. The task transitions to INPUT_REQUIRED with a sentinel
        payload so the trigger loop routes to sampling instead of elicitation.
        Use await_sampling_response(task_id) to block for the reply.

        Returns {"s": "ok", "seq": N}.
        """
        _require_initialized()

        store = state["task_store"]
        task = await store.get(TaskId(value=task_id))
        if task is None:
            return {"s": "err", "code": "TASK_NOT_FOUND"}
        if task.status.is_terminal:
            return {"s": "err", "code": "TASK_ALREADY_TERMINAL", "status": task.status.value}

        # Sampling requests reuse the input round-trip plumbing with a
        # sentinel-marked prompt so the trigger loop can distinguish.
        try:
            task.request_input(
                prompt="__metis_sampling__",
                schema={
                    "_metis_sampling": True,
                    "messages": messages,
                    "system": system,
                    "max_tokens": max_tokens,
                },
            )
        except ValueError as e:
            return {"s": "err", "code": "INVALID_TRANSITION", "message": str(e)}
        await store.update(task)
        return {"s": "ok", "seq": task.input_seq}

    @mcp.tool()
    async def await_sampling_response(
        task_id: str,
        seq: int,
        timeout: float = 55.0,
    ) -> dict[str, Any]:
        """Long-poll for the client's LLM completion. Mirror of await_input_response."""
        _require_initialized()

        store = state["task_store"]
        tid = TaskId(value=task_id)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = await store.get(tid)
            if task is None:
                return {"s": "err", "code": "TASK_NOT_FOUND"}
            if task.status.value == "cancelled":
                return {"s": "cancelled"}
            if task.status.is_terminal:
                return {"s": "err", "code": "TASK_ALREADY_TERMINAL", "status": task.status.value}
            if task.input_seq >= seq and task.input_response is not None:
                return {"s": "resp", "response": task.input_response}
            await asyncio.sleep(0.25)
        return {"s": "timeout"}

    return WorkerToolsHandle(
        poll=poll,
        deliver=deliver,
        probe=probe,
        report_progress=report_progress,
        check_cancelled=check_cancelled,
        request_input=request_input,
        await_input_response=await_input_response,
        request_sampling=request_sampling,
        await_sampling_response=await_sampling_response,
        lifespan=lifespan,
    )
