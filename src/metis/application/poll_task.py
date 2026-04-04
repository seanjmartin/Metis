"""Use case: poll for a pending task (used by the worker/dispatcher).

Supports both instant polling and long-polling. Long-poll holds the call
open server-side, returning immediately when a task appears or after
the timeout expires. This minimizes idle token cost for LLM dispatchers.

NOT responsible for:
- Task execution (see dispatcher agent)
- Result delivery (see DeliverResultUseCase)
- Timeout discovery (see dispatcher prompt / calling code)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from metis.domain.entities import Heartbeat, Task
from metis.domain.errors import Ok, Result
from metis.domain.protocols import HeartbeatStore, TaskStore
from metis.domain.value_objects import WorkerId

_LONG_POLL_INTERVAL_SECONDS = 1.0
_HEARTBEAT_INTERVAL_SECONDS = 15.0


@dataclass(frozen=True)
class PollTaskInput:
    worker_id: str
    capabilities: list[str] = field(default_factory=list)
    timeout_seconds: float = 0


class PollTaskUseCase:
    """Claims the next available task and updates the worker heartbeat.

    When timeout_seconds > 0, blocks server-side until a task appears or
    the timeout expires. Updates heartbeat periodically during the wait
    so is_worker_alive() doesn't report false negatives.

    NOT responsible for:
    - Task execution (see dispatcher agent)
    - Deciding what to do with the task (see presentation layer)
    - Choosing the right timeout (see dispatcher prompt / calling code)
    """

    def __init__(
        self, task_store: TaskStore, heartbeat_store: HeartbeatStore
    ) -> None:
        self._task_store = task_store
        self._heartbeat_store = heartbeat_store

    async def execute(self, input: PollTaskInput) -> Result[Task | None]:
        worker_id = WorkerId(value=input.worker_id)

        await self._update_heartbeat(worker_id, input.capabilities)
        await self._task_store.expire_stale(datetime.now(UTC))

        task = await self._task_store.claim_next(input.capabilities, worker_id)
        if task is not None or input.timeout_seconds <= 0:
            return Ok(task)

        # Long-poll: block until a task appears or timeout expires
        elapsed = 0.0
        since_heartbeat = 0.0

        while elapsed < input.timeout_seconds:
            await asyncio.sleep(_LONG_POLL_INTERVAL_SECONDS)
            elapsed += _LONG_POLL_INTERVAL_SECONDS
            since_heartbeat += _LONG_POLL_INTERVAL_SECONDS

            if since_heartbeat >= _HEARTBEAT_INTERVAL_SECONDS:
                await self._update_heartbeat(worker_id, input.capabilities)
                since_heartbeat = 0.0

            task = await self._task_store.claim_next(input.capabilities, worker_id)
            if task is not None:
                return Ok(task)

        # Final heartbeat before returning empty
        await self._update_heartbeat(worker_id, input.capabilities)
        return Ok(None)

    async def _update_heartbeat(
        self, worker_id: WorkerId, capabilities: list[str]
    ) -> None:
        await self._heartbeat_store.upsert(
            Heartbeat(
                worker_id=worker_id,
                capabilities=capabilities,
                last_seen=datetime.now(UTC),
            )
        )
