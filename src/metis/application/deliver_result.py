"""Use case: deliver a completed result for a claimed task.

NOT responsible for:
- Task claiming (see PollTaskUseCase)
- Notifying the enqueuer (see WaitForResultUseCase polling)
"""

from __future__ import annotations

from dataclasses import dataclass

from metis.domain.errors import Err, Ok, Result, TaskNotFoundError
from metis.domain.protocols import TaskStore
from metis.domain.value_objects import TaskId


@dataclass(frozen=True)
class DeliverResultInput:
    task_id: str
    result: dict


class DeliverResultUseCase:
    """Completes a claimed task with the worker's result.

    NOT responsible for:
    - Polling for tasks (see PollTaskUseCase)
    - Marking tasks as consumed (see WaitForResultUseCase)
    """

    def __init__(self, task_store: TaskStore) -> None:
        self._task_store = task_store

    async def execute(self, input: DeliverResultInput) -> Result[None]:
        task_id = TaskId(value=input.task_id)
        task = await self._task_store.get(task_id)

        if task is None:
            return Err(TaskNotFoundError(message=f"Task {input.task_id} not found"))

        try:
            task.complete(input.result)
        except ValueError as e:
            return Err(TaskNotFoundError(message=str(e)))

        await self._task_store.update(task)
        return Ok(None)
