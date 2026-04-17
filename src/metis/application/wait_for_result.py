"""Use case: wait for a task's result with timeout.

NOT responsible for:
- Task execution (see dispatcher agent)
- Enqueueing tasks (see EnqueueTaskUseCase)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from metis.domain.errors import (
    Err,
    Ok,
    Result,
    TaskCancelledError,
    TaskExpiredError,
    TaskFailedError,
    TaskNotFoundError,
)
from metis.domain.protocols import TaskStore
from metis.domain.value_objects import TaskId, TaskStatus

_POLL_INTERVAL_SECONDS = 0.1


@dataclass(frozen=True)
class WaitForResultInput:
    task_id: str
    timeout_seconds: float = 300.0


class WaitForResultUseCase:
    """Polls the task store until a result is available or timeout expires.

    NOT responsible for:
    - Task creation (see EnqueueTaskUseCase)
    - Result delivery (see DeliverResultUseCase)
    """

    def __init__(self, task_store: TaskStore) -> None:
        self._task_store = task_store

    async def execute(self, input: WaitForResultInput) -> Result[dict | None]:
        task_id = TaskId(value=input.task_id)
        elapsed = 0.0

        while elapsed < input.timeout_seconds:
            task = await self._task_store.get(task_id)

            if task is None:
                return Err(TaskNotFoundError(message=f"Task {input.task_id} not found"))

            if task.status == TaskStatus.COMPLETE:
                await self._task_store.mark_consumed(task_id)
                return Ok(task.result)

            if task.status == TaskStatus.EXPIRED:
                return Err(TaskExpiredError(message=f"Task {input.task_id} expired"))

            if task.status == TaskStatus.CANCELLED:
                return Err(TaskCancelledError(message=f"Task {input.task_id} was cancelled"))

            if task.status == TaskStatus.FAILED:
                msg = task.error_message or "task failed"
                code = task.error_code or "TASK_FAILED"
                return Err(TaskFailedError(message=f"{code}: {msg}"))

            if task.status == TaskStatus.CONSUMED:
                return Ok(None)

            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            elapsed += _POLL_INTERVAL_SECONDS

        return Ok(None)
