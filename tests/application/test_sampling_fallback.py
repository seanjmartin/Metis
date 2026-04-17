"""Tests for sampling fallback when no dispatcher is alive."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from metis import TaskQueue
from metis.domain.value_objects import TaskStatus
from tests.application.test_elicitation_roundtrip import FakeContext


def _seed_heartbeat(queue: TaskQueue, worker_id: str = "dispatcher") -> None:
    """Insert a fresh heartbeat so is_worker_alive() returns True."""
    conn = queue._get_sync_conn()
    conn.execute(
        "INSERT INTO heartbeats (worker_id, capabilities, last_seen) VALUES (?, ?, ?)",
        (worker_id, json.dumps([]), datetime.now(UTC).isoformat()),
    )
    conn.commit()


class TestSamplingFallback:
    async def test_enqueue_falls_back_when_dispatcher_dead(self, tmp_path: Path) -> None:
        """No dispatcher heartbeat → enqueue_with_sampling_fallback completes via ctx."""
        db_path = str(tmp_path / "fb.db")
        async with TaskQueue(db_path=db_path) as queue:
            assert queue.is_worker_alive() is False

            ctx = FakeContext(sampling_text="a sampled reply")
            tid = await queue.enqueue_with_sampling_fallback(
                type="classify",
                payload={"instructions": "is this spam?"},
                ctx=ctx,
            )

            status = queue.get_task_status(tid)
            assert status == TaskStatus.COMPLETE
            assert len(ctx.sampling_calls) == 1

            result = await queue.wait_for_result(tid, timeout=1.0)
            assert result is not None
            assert result["sampled_response"] == "a sampled reply"
            assert result["metis"]["fallback"] == "sampling"

    async def test_skips_fallback_when_dispatcher_alive(self, tmp_path: Path) -> None:
        """If a dispatcher is alive the task stays PENDING; no sampling is issued."""
        db_path = str(tmp_path / "alive.db")
        async with TaskQueue(db_path=db_path) as queue:
            _seed_heartbeat(queue)
            assert queue.is_worker_alive() is True

            ctx = FakeContext(sampling_text="should never be used")
            tid = await queue.enqueue_with_sampling_fallback(
                type="classify",
                payload={"instructions": "task"},
                ctx=ctx,
            )

            status = queue.get_task_status(tid)
            assert status == TaskStatus.PENDING
            assert ctx.sampling_calls == []  # no sampling attempted

    async def test_leaves_task_pending_when_sampling_fails(self, tmp_path: Path) -> None:
        """If sampling raises, the task stays PENDING so a future dispatcher can claim it."""
        db_path = str(tmp_path / "fail.db")
        async with TaskQueue(db_path=db_path) as queue:

            class FailingSession:
                async def create_message(self, **kwargs):  # noqa: ANN003, ANN202
                    raise RuntimeError("sampling rejected by client")

            class FailingCtx:
                session = FailingSession()

            tid = await queue.enqueue_with_sampling_fallback(
                type="classify",
                payload={"instructions": "task"},
                ctx=FailingCtx(),
            )

            assert queue.get_task_status(tid) == TaskStatus.PENDING


class TestBuildSamplingRequest:
    """Direct unit coverage for the payload-to-sampling-request translator."""

    def test_extracts_instructions_as_user_message(self) -> None:
        from metis.application.sampling_fallback import build_sampling_request

        req = build_sampling_request("classify", {"instructions": "hi"})
        assert req.messages == [{"role": "user", "content": "hi"}]
        assert req.system and "classify" in req.system
        assert req.max_tokens == 1024

    def test_uses_explicit_messages_when_provided(self) -> None:
        from metis.application.sampling_fallback import build_sampling_request

        req = build_sampling_request(
            "custom",
            {
                "messages": [
                    {"role": "user", "content": "one"},
                    {"role": "assistant", "content": "two"},
                ],
                "system": "be brief",
                "max_tokens": 128,
            },
        )
        assert len(req.messages) == 2
        assert req.system == "be brief"
        assert req.max_tokens == 128
