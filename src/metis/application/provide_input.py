"""Use case: write a user response into an INPUT_REQUIRED task.

NOT responsible for:
- Requesting the input (see RequestInputUseCase used by the dispatcher)
- Rendering the prompt to the user (that's the trigger-side ctx.elicit loop)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from metis.domain.errors import (
    Err,
    InvalidTransitionError,
    Ok,
    Result,
    TaskAlreadyTerminalError,
    TaskNotFoundError,
)
from metis.domain.protocols import TaskStore
from metis.domain.value_objects import TaskId


@dataclass(frozen=True)
class ProvideInputInput:
    task_id: str
    response: dict[str, Any]


class ProvideInputUseCase:
    """Transitions a task from INPUT_REQUIRED back to CLAIMED with the response attached.

    Rejects if the task is terminal or not currently awaiting input.
    """

    def __init__(self, task_store: TaskStore) -> None:
        self._task_store = task_store

    async def execute(self, input: ProvideInputInput) -> Result[None]:
        task_id = TaskId(value=input.task_id)
        task = await self._task_store.get(task_id)

        if task is None:
            return Err(TaskNotFoundError(message=f"Task {input.task_id} not found"))

        if task.status.is_terminal:
            return Err(
                TaskAlreadyTerminalError(
                    message=(
                        f"Task {input.task_id} is in terminal status "
                        f"{task.status.value!r}; provide_input rejected"
                    )
                )
            )

        try:
            task.provide_input(input.response)
        except ValueError as e:
            return Err(InvalidTransitionError(message=str(e)))

        await self._task_store.update(task)
        return Ok(None)
