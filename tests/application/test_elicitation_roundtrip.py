"""End-to-end tests for the elicitation and sampling round-trip through TaskQueue.

Verifies that when a task enters INPUT_REQUIRED, wait_for_result (with a
fake ctx) calls ctx.elicit or ctx.session.create_message, writes the
response back, and resumes waiting. The transparent loop is invisible to
the caller.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from metis import TaskQueue
from metis.domain.value_objects import WorkerId
from metis.infrastructure.database import init_async_database
from metis.infrastructure.sqlite_task_store import SqliteTaskStore


class FakeElicitResult:
    def __init__(self, action: str, data_response: str | None) -> None:
        self.action = action

        class _Data:
            pass

        d = _Data()
        if data_response is not None:
            d.response = data_response
        self.data = d if data_response is not None else None


class FakeContext:
    """Minimal ctx stand-in capturing progress + elicit + sampling calls."""

    def __init__(
        self,
        elicit_response: str | None = "OK",
        sampling_text: str | None = "sampled!",
        elicit_action: str = "accept",
    ) -> None:
        self.progress_calls: list[dict[str, Any]] = []
        self.elicit_calls: list[dict[str, Any]] = []
        self.sampling_calls: list[dict[str, Any]] = []
        self._elicit_response = elicit_response
        self._elicit_action = elicit_action
        self._sampling_text = sampling_text
        self.session = _FakeSession(self)

    async def report_progress(
        self,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> None:
        self.progress_calls.append({"progress": progress, "total": total, "message": message})

    async def elicit(self, message: str, schema: type) -> FakeElicitResult:
        self.elicit_calls.append({"message": message, "schema": schema})
        return FakeElicitResult(self._elicit_action, self._elicit_response)


class _FakeSession:
    def __init__(self, parent: FakeContext) -> None:
        self._parent = parent

    async def create_message(self, **kwargs: Any) -> Any:
        self._parent.sampling_calls.append(kwargs)

        class _Content:
            text = self._parent._sampling_text

        class _Result:
            content = _Content()
            model = "fake-model"

        return _Result()


async def _dispatcher_elicit_then_complete(db_path: str, task_id_value: str, prompt: str) -> None:
    """Simulate a dispatcher: claim, request input, wait for response, complete."""
    conn = await init_async_database(db_path)
    try:
        store = SqliteTaskStore(conn)

        # Claim the task
        claimed = await store.claim_next([], WorkerId(value="dispatcher"))
        assert claimed is not None

        # Request input
        claimed.request_input(prompt, {"type": "object"})
        await store.update(claimed)

        # Wait briefly for the wait_for_result loop to provide input
        from metis.domain.value_objects import TaskId, TaskStatus

        tid = TaskId(value=task_id_value)
        for _ in range(60):
            await asyncio.sleep(0.1)
            task = await store.get(tid)
            assert task is not None
            if task.status == TaskStatus.CLAIMED and task.input_response is not None:
                # Input provided — complete the task using the response
                task.complete({"echoed": task.input_response})
                await store.update(task)
                return

        raise AssertionError("input response never arrived")
    finally:
        await conn.close()


class TestElicitationRoundTrip:
    async def test_transparent_elicit_and_complete(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "elicit.db")
        async with TaskQueue(db_path=db_path) as queue:
            tid = queue.enqueue(type="q", payload={}, ttl_seconds=60)

            ctx = FakeContext(elicit_response="hello world")

            # Run dispatcher and wait_for_result concurrently
            dispatcher = asyncio.create_task(
                _dispatcher_elicit_then_complete(db_path, tid.value, "What is your name?")
            )
            waiter = asyncio.create_task(queue.wait_for_result(tid, timeout=15.0, ctx=ctx))

            result = await waiter
            await dispatcher

            assert result is not None
            assert result["echoed"]["response"] == "hello world"
            assert len(ctx.elicit_calls) == 1
            assert ctx.elicit_calls[0]["message"] == "What is your name?"

    async def test_client_declines_fails_the_task(self, tmp_path: Path) -> None:
        from metis.domain.errors import MetisException, TaskFailedError

        db_path = str(tmp_path / "decline.db")
        async with TaskQueue(db_path=db_path) as queue:
            tid = queue.enqueue(type="q", payload={}, ttl_seconds=60)

            ctx = FakeContext(elicit_response=None, elicit_action="decline")

            async def dispatcher_just_asks() -> None:
                conn = await init_async_database(db_path)
                try:
                    store = SqliteTaskStore(conn)
                    claimed = await store.claim_next([], WorkerId(value="w1"))
                    assert claimed is not None
                    claimed.request_input("tell me", {"type": "object"})
                    await store.update(claimed)
                finally:
                    await conn.close()

            dispatcher = asyncio.create_task(dispatcher_just_asks())
            try:
                await queue.wait_for_result(tid, timeout=10.0, ctx=ctx)
                raise AssertionError("expected MetisException for failed task")
            except MetisException as e:
                assert isinstance(e.error, TaskFailedError)
            finally:
                await dispatcher


class TestSamplingRoundTrip:
    async def test_sampling_routes_through_session_create_message(self, tmp_path: Path) -> None:
        """Dispatcher requests sampling via the sentinel schema."""
        db_path = str(tmp_path / "sample.db")
        async with TaskQueue(db_path=db_path) as queue:
            tid = queue.enqueue(type="q", payload={}, ttl_seconds=60)

            ctx = FakeContext(sampling_text="the model says hi")

            async def dispatcher_requests_sampling() -> None:
                conn = await init_async_database(db_path)
                try:
                    store = SqliteTaskStore(conn)
                    claimed = await store.claim_next([], WorkerId(value="w1"))
                    assert claimed is not None
                    claimed.request_input(
                        "__metis_sampling__",
                        {
                            "_metis_sampling": True,
                            "messages": [{"role": "user", "content": "hi"}],
                            "system": "be brief",
                            "max_tokens": 50,
                        },
                    )
                    await store.update(claimed)

                    # Wait for the response and complete
                    from metis.domain.value_objects import TaskId, TaskStatus

                    for _ in range(60):
                        await asyncio.sleep(0.1)
                        task = await store.get(TaskId(value=tid.value))
                        assert task is not None
                        if task.status == TaskStatus.CLAIMED and task.input_response is not None:
                            task.complete({"used": task.input_response})
                            await store.update(task)
                            return
                finally:
                    await conn.close()

            dispatcher = asyncio.create_task(dispatcher_requests_sampling())
            result = await queue.wait_for_result(tid, timeout=15.0, ctx=ctx)
            await dispatcher

            assert result is not None
            assert len(ctx.sampling_calls) == 1
            assert ctx.sampling_calls[0]["system_prompt"] == "be brief"
            assert result["used"]["response"] == "the model says hi"


class TestProgressForwarding:
    async def test_progress_updates_flow_through_to_ctx(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "progress.db")
        async with TaskQueue(db_path=db_path) as queue:
            tid = queue.enqueue(type="q", payload={}, ttl_seconds=60)

            ctx = FakeContext()

            async def dispatcher_with_progress() -> None:
                from metis.infrastructure.sqlite_progress_store import (
                    SqliteProgressStore,
                )

                conn = await init_async_database(db_path)
                try:
                    store = SqliteTaskStore(conn)
                    prog = SqliteProgressStore(conn)
                    claimed = await store.claim_next([], WorkerId(value="w1"))
                    assert claimed is not None

                    await prog.append(tid, progress=0.25, message="step 1")
                    await asyncio.sleep(0.3)
                    await prog.append(tid, progress=0.75, message="step 2")
                    await asyncio.sleep(0.3)

                    claimed.complete({"done": True})
                    await store.update(claimed)
                finally:
                    await conn.close()

            dispatcher = asyncio.create_task(dispatcher_with_progress())
            result = await queue.wait_for_result(tid, timeout=15.0, ctx=ctx)
            await dispatcher

            assert result == {"done": True}
            assert len(ctx.progress_calls) >= 2
            assert any(c["message"] == "step 1" for c in ctx.progress_calls)
            assert any(c["message"] == "step 2" for c in ctx.progress_calls)
