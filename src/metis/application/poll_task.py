"""Use case: poll for a pending task (used by the worker/dispatcher).

NOT responsible for:
- Task execution (see dispatcher agent)
- Result delivery (see DeliverResultUseCase)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from metis.domain.entities import Heartbeat, Task
from metis.domain.errors import Ok, Result
from metis.domain.protocols import HeartbeatStore, TaskStore
from metis.domain.value_objects import WorkerId


@dataclass(frozen=True)
class PollTaskInput:
    worker_id: str
    capabilities: list[str]


class PollTaskUseCase:
    """Claims the next available task and updates the worker heartbeat.

    NOT responsible for:
    - Task execution (see dispatcher agent)
    - Deciding what to do with the task (see presentation layer)
    """

    def __init__(
        self, task_store: TaskStore, heartbeat_store: HeartbeatStore
    ) -> None:
        self._task_store = task_store
        self._heartbeat_store = heartbeat_store

    async def execute(self, input: PollTaskInput) -> Result[Task | None]:
        worker_id = WorkerId(value=input.worker_id)

        await self._heartbeat_store.upsert(
            Heartbeat(
                worker_id=worker_id,
                capabilities=input.capabilities,
                last_seen=datetime.now(UTC),
            )
        )

        await self._task_store.expire_stale(datetime.now(UTC))

        task = await self._task_store.claim_next(input.capabilities, worker_id)
        return Ok(task)
