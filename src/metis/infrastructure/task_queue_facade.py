"""Public facade for the Metis task queue — the deep module.

This is the primary integration point for MCP servers. It hides the
layered internals behind a 3-method API.

NOT responsible for:
- Executing tasks (see dispatcher agent)
- Exposing MCP tools (see metis.presentation.worker_server)
- Managing dispatcher lifecycle (see self-healing protocol)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from metis.domain.value_objects import TaskId, TaskStatus
from metis.infrastructure.database import SCHEMA_SQL, _run_migrations

if TYPE_CHECKING:
    # Imported purely for type hints. `mcp` is an optional dependency (worker extra)
    # so we must not import it at runtime from core infrastructure.
    from mcp.server.fastmcp import Context

_POLL_INTERVAL_SECONDS = 0.1
_PROGRESS_DRAIN_INTERVAL_SECONDS = 0.25
_INPUT_SCHEMA_SAMPLING_KEY = "_metis_sampling"

_log = logging.getLogger(__name__)


class TaskQueue:
    """Enqueue tasks and wait for results via SQLite.

    This is the public API for integrating MCP servers. Usage:

        queue = TaskQueue(db_path="~/.my-server/metis.db")
        task_id = queue.enqueue(Task(type="classify", payload={...}))
        result = await queue.wait_for_result(task_id, timeout=60)

    NOT responsible for:
    - Executing tasks (see dispatcher agent)
    - Exposing MCP tools (see metis.presentation.worker_server)
    - Managing dispatcher lifecycle (see self-healing protocol)
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = str(Path(db_path).expanduser())
        self._sync_conn: sqlite3.Connection | None = None

    def _get_sync_conn(self) -> sqlite3.Connection:
        if self._sync_conn is None:
            path = Path(self._db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._sync_conn = sqlite3.connect(self._db_path)
            self._sync_conn.row_factory = sqlite3.Row
            self._sync_conn.execute("PRAGMA journal_mode=WAL")
            self._sync_conn.execute("PRAGMA busy_timeout=5000")
            self._sync_conn.executescript(SCHEMA_SQL)
            self._sync_conn.commit()
            _run_migrations(self._sync_conn)
        return self._sync_conn

    def enqueue(
        self,
        *,
        type: str,
        payload: dict[str, Any],
        priority: int = 0,
        ttl_seconds: int = 300,
        capabilities_required: list[str] | None = None,
        session_id: str | None = None,
    ) -> TaskId:
        """Create and enqueue a new task. Synchronous — safe from any context.

        Returns the TaskId for later retrieval via wait_for_result().
        """
        conn = self._get_sync_conn()
        task_id = TaskId.generate()
        now = datetime.now(UTC)

        conn.execute(
            """
            INSERT INTO tasks (id, type, payload, status, priority,
                               ttl_seconds, created_at, capabilities_required,
                               session_id)
            VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)
            """,
            (
                task_id.value,
                type,
                json.dumps(payload),
                priority,
                ttl_seconds,
                now.isoformat(),
                json.dumps(capabilities_required or []),
                session_id,
            ),
        )
        conn.commit()

        # Lazily expire stale non-terminal tasks
        conn.execute(
            """
            UPDATE tasks SET status = 'expired'
            WHERE status IN ('pending', 'claimed', 'input_required')
              AND datetime(created_at, '+' || ttl_seconds || ' seconds') <= datetime(?)
            """,
            (now.isoformat(),),
        )
        conn.commit()

        return task_id

    async def enqueue_with_sampling_fallback(
        self,
        *,
        type: str,
        payload: dict[str, Any],
        ctx: Any,
        priority: int = 0,
        ttl_seconds: int = 300,
        capabilities_required: list[str] | None = None,
        session_id: str | None = None,
    ) -> TaskId:
        """Enqueue a task; if no dispatcher is alive, complete it via MCP sampling.

        When the host MCP client supports sampling (`ctx.session.create_message`),
        this synthesises a completed task row using the client's LLM, so the
        caller's flow still works when the dispatcher is offline.

        A `metis: {"fallback": "sampling"}` marker is written into the result
        so callers can tell the answer came from sampling, not the dispatcher.
        """
        from metis.application.sampling_fallback import build_sampling_request

        task_id = self.enqueue(
            type=type,
            payload=payload,
            priority=priority,
            ttl_seconds=ttl_seconds,
            capabilities_required=capabilities_required,
            session_id=session_id,
        )

        if self.is_worker_alive():
            return task_id

        # Dispatcher is down — try sampling
        request = build_sampling_request(type, payload)
        response = await self._sample_via_ctx(
            ctx,
            {
                "messages": request.messages,
                "system": request.system,
                "max_tokens": request.max_tokens,
            },
        )
        if response is None:
            # Sampling failed; leave the task pending so a future dispatcher can claim it
            return task_id

        # Mark the task CLAIMED→COMPLETE synthetically
        from metis.infrastructure.database import init_async_database
        from metis.infrastructure.sqlite_task_store import SqliteTaskStore

        conn = await init_async_database(self._db_path)
        try:
            store = SqliteTaskStore(conn)
            task = await store.get(task_id)
            if task is None:
                return task_id
            from metis.domain.value_objects import WorkerId

            task.claim(WorkerId(value="__metis_sampling_fallback__"))
            task.complete(
                {
                    "sampled_response": response.get("response"),
                    "model": response.get("model"),
                    "metis": {"fallback": "sampling"},
                }
            )
            await store.update(task)
        finally:
            await conn.close()

        return task_id

    async def wait_for_result(
        self,
        task_id: TaskId,
        timeout: float = 300.0,
        ctx: Context | None = None,
    ) -> dict[str, Any] | None:
        """Wait for a task's result. Async — polls until result or timeout.

        Returns the result dict, or None if timeout expires or already consumed.
        Raises MetisException wrapping a typed MetisError (TaskExpiredError,
        TaskCancelledError, TaskFailedError, TaskNotFoundError) on failure.

        When ctx (a FastMCP Context) is provided:
        - Progress reports from the dispatcher are forwarded to the client via
          ctx.report_progress().
        - INPUT_REQUIRED transitions are handled transparently: the dispatcher's
          prompt is surfaced to the client via ctx.elicit() (or ctx.session.
          create_message() for sampling-sentinel prompts), the response is
          written back, and the wait loop continues.

        The caller sees a single round-trip that eventually returns the final
        result; elicitation and progress are invisible plumbing.
        """
        from metis.domain.errors import (
            MetisException,
            TaskCancelledError,
            TaskExpiredError,
            TaskFailedError,
            TaskNotFoundError,
        )
        from metis.domain.value_objects import TaskStatus
        from metis.infrastructure.database import init_async_database
        from metis.infrastructure.sqlite_progress_store import SqliteProgressStore
        from metis.infrastructure.sqlite_task_store import SqliteTaskStore

        conn = await init_async_database(self._db_path)
        try:
            task_store = SqliteTaskStore(conn)
            progress_store = SqliteProgressStore(conn)

            drain_task: asyncio.Task[None] | None = None
            if ctx is not None and hasattr(ctx, "report_progress"):
                drain_task = asyncio.create_task(self._drain_progress(task_id, progress_store, ctx))

            try:
                elapsed = 0.0
                while elapsed < timeout:
                    task = await task_store.get(task_id)
                    if task is None:
                        raise MetisException(
                            TaskNotFoundError(message=f"Task {task_id.value} not found")
                        )

                    if task.status == TaskStatus.COMPLETE:
                        await task_store.mark_consumed(task_id)
                        return task.result

                    if task.status == TaskStatus.EXPIRED:
                        raise MetisException(
                            TaskExpiredError(message=f"Task {task_id.value} expired")
                        )

                    if task.status == TaskStatus.CANCELLED:
                        raise MetisException(
                            TaskCancelledError(message=f"Task {task_id.value} was cancelled")
                        )

                    if task.status == TaskStatus.FAILED:
                        msg = task.error_message or "task failed"
                        code = task.error_code or "TASK_FAILED"
                        raise MetisException(TaskFailedError(message=f"{code}: {msg}"))

                    if task.status == TaskStatus.CONSUMED:
                        return None

                    if task.status == TaskStatus.INPUT_REQUIRED and ctx is not None:
                        await self._handle_input_required(task, task_store, ctx)
                        # loop again; task should now be CLAIMED or terminal

                    await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                    elapsed += _POLL_INTERVAL_SECONDS

                return None
            finally:
                if drain_task is not None:
                    drain_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await drain_task
        finally:
            await conn.close()

    async def _drain_progress(
        self,
        task_id: TaskId,
        store: Any,
        ctx: Any,
    ) -> None:
        """Tail progress updates for a task and forward them to ctx.report_progress.

        Runs until cancelled; swallows exceptions from ctx since clients may not
        all support progress notifications.
        """
        last_seq = 0
        while True:
            try:
                updates = await store.tail_since(task_id, last_seq)
            except Exception:
                await asyncio.sleep(_PROGRESS_DRAIN_INTERVAL_SECONDS)
                continue
            for update in updates:
                last_seq = update.seq
                try:
                    await ctx.report_progress(
                        progress=update.progress,
                        total=update.total,
                        message=update.message,
                    )
                except Exception as e:
                    _log.debug("progress forward failed (ignored): %s", e)
            await asyncio.sleep(_PROGRESS_DRAIN_INTERVAL_SECONDS)

    async def _handle_input_required(
        self,
        task: Any,
        task_store: Any,
        ctx: Any,
    ) -> None:
        """Route an INPUT_REQUIRED task through elicitation or sampling.

        Elicitation path: ctx.elicit(message=prompt, schema=MetisElicitResponse).
        Sampling path: ctx.session.create_message(...) — detected via the
        '_metis_sampling' sentinel key in task.input_schema.

        Writes the response into the task and transitions back to CLAIMED.
        If the client cannot satisfy the request, marks the task FAILED so
        the dispatcher doesn't hang.
        """
        schema = task.input_schema or {}
        is_sampling = isinstance(schema, dict) and schema.get(_INPUT_SCHEMA_SAMPLING_KEY)

        if is_sampling:
            response = await self._sample_via_ctx(ctx, schema)
        else:
            response = await self._elicit_via_ctx(ctx, task.input_prompt or "")

        if response is None:
            # Client declined/cancelled the elicitation → fail the task
            task.fail(
                "CLIENT_DECLINED",
                "Client declined to provide input or sampling response",
            )
            await task_store.update(task)
            return

        task.provide_input(response)
        await task_store.update(task)

    @staticmethod
    async def _elicit_via_ctx(ctx: Any, prompt: str) -> dict[str, Any] | None:
        from pydantic import BaseModel, Field

        class MetisElicitResponse(BaseModel):
            response: str = Field(
                description=(
                    "Your response to the dispatcher's question. "
                    "If the question expects structured data, return JSON as a string."
                )
            )

        try:
            result = await ctx.elicit(message=prompt, schema=MetisElicitResponse)
        except Exception as e:
            _log.warning("ctx.elicit failed: %s", e)
            return None

        # mcp 1.25.0: result has .action ('accept' | 'decline' | 'cancel') and .data
        action = getattr(result, "action", "accept")
        if action != "accept":
            return None
        data = getattr(result, "data", None)
        if data is None:
            return None
        # data is the parsed Pydantic model; extract the response field
        text = getattr(data, "response", None)
        if text is None:
            return None
        return {"response": text}

    @staticmethod
    async def _sample_via_ctx(ctx: Any, schema: dict[str, Any]) -> dict[str, Any] | None:
        from mcp.types import SamplingMessage, TextContent

        messages_raw = schema.get("messages", [])
        system = schema.get("system")
        max_tokens = schema.get("max_tokens") or 1024

        try:
            messages = [
                SamplingMessage(
                    role=m.get("role", "user"),
                    content=TextContent(type="text", text=m.get("content", "")),
                )
                for m in messages_raw
            ]
        except Exception as e:
            _log.warning("sampling message construction failed: %s", e)
            return None

        try:
            result = await ctx.session.create_message(
                messages=messages,
                max_tokens=max_tokens,
                system_prompt=system,
            )
        except Exception as e:
            _log.warning("ctx.session.create_message failed: %s", e)
            return None

        content = getattr(result, "content", None)
        text = getattr(content, "text", None) if content else None
        return {
            "response": text if text is not None else str(result),
            "model": getattr(result, "model", None),
        }

    async def cancel(self, task_id: TaskId, session_id: str | None = None) -> None:
        """Cancel a non-terminal task. Async — opens an ephemeral connection.

        When ``session_id`` is provided, the task's session_id must match or
        the cancel is rejected with TaskNotFoundError (no task-enumeration
        leak). Leave ``session_id`` as None in single-tenant deployments.

        Raises MetisException wrapping TaskAlreadyTerminalError if the task
        is already in a terminal state, or TaskNotFoundError if no such task
        (or the task belongs to a different session).
        """
        from metis.application.cancel_task import CancelTaskInput, CancelTaskUseCase
        from metis.domain.errors import MetisException
        from metis.infrastructure.database import init_async_database
        from metis.infrastructure.sqlite_task_store import SqliteTaskStore

        conn = await init_async_database(self._db_path)
        try:
            use_case = CancelTaskUseCase(task_store=SqliteTaskStore(conn))
            result = await use_case.execute(
                CancelTaskInput(task_id=task_id.value, session_id=session_id)
            )
            if result.is_error:
                raise MetisException(result.error)
        finally:
            await conn.close()

    def is_worker_alive(self, timeout_seconds: int = 60) -> bool:
        """Check if any dispatcher worker has a recent heartbeat. Synchronous."""
        conn = self._get_sync_conn()
        row = conn.execute(
            "SELECT last_seen FROM heartbeats ORDER BY last_seen DESC LIMIT 1"
        ).fetchone()

        if row is None:
            return False

        last_seen = datetime.fromisoformat(row["last_seen"])
        return datetime.now(UTC) < last_seen + timedelta(seconds=timeout_seconds)

    def get_task_status(self, task_id: TaskId) -> TaskStatus | None:
        """Return the current TaskStatus of a task, or None if not found. Synchronous."""
        conn = self._get_sync_conn()
        row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id.value,)).fetchone()
        if row is None:
            return None
        return TaskStatus(row["status"])

    async def provide_input(self, task_id: TaskId, response: dict[str, Any]) -> None:
        """Write a user response into an INPUT_REQUIRED task and transition back to CLAIMED.

        Raises MetisException if the task isn't awaiting input, or is terminal, or missing.
        """
        from metis.application.provide_input import (
            ProvideInputInput,
            ProvideInputUseCase,
        )
        from metis.domain.errors import MetisException
        from metis.infrastructure.database import init_async_database
        from metis.infrastructure.sqlite_task_store import SqliteTaskStore

        conn = await init_async_database(self._db_path)
        try:
            use_case = ProvideInputUseCase(task_store=SqliteTaskStore(conn))
            result = await use_case.execute(
                ProvideInputInput(task_id=task_id.value, response=response)
            )
            if result.is_error:
                raise MetisException(result.error)
        finally:
            await conn.close()

    def get_task_tokens(self, task_id: TaskId) -> tuple[int | None, int | None]:
        """Get token counts for a completed task. Synchronous.

        Returns (input_tokens, output_tokens). Both may be None if not reported.
        """
        conn = self._get_sync_conn()
        row = conn.execute(
            "SELECT input_tokens, output_tokens FROM tasks WHERE id = ?",
            (task_id.value,),
        ).fetchone()

        if row is None:
            return None, None

        return row["input_tokens"], row["output_tokens"]

    def close(self) -> None:
        """Close the underlying sync connection."""
        if self._sync_conn is not None:
            self._sync_conn.close()
            self._sync_conn = None

    def __enter__(self) -> TaskQueue:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    async def __aenter__(self) -> TaskQueue:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
