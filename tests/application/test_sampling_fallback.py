"""Tests for sampling fallback when no dispatcher is alive."""

from __future__ import annotations

from pathlib import Path

from metis import TaskQueue
from tests.application.test_elicitation_roundtrip import FakeContext


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
            # Fallback completed the task synthetically
            from metis.domain.value_objects import TaskStatus

            assert status == TaskStatus.COMPLETE
            assert len(ctx.sampling_calls) == 1

            # wait_for_result returns the sampled response with metis marker
            result = await queue.wait_for_result(tid, timeout=1.0)
            assert result is not None
            assert result["sampled_response"] == "a sampled reply"
            assert result["metis"]["fallback"] == "sampling"
