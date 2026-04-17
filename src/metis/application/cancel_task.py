"""Use case: cancel a non-terminal task.

NOT responsible for:
- Notifying a dispatcher that has already claimed the task
  (dispatcher observes cancellation on its next interaction)
- Cleaning up spawned sub-agents (dispatcher's responsibility)
"""

from __future__ import annotations

from dataclasses import dataclass

from metis.domain.errors import (
    Err,
    Ok,
    Result,
    TaskAlreadyTerminalError,
    TaskNotFoundError,
)
from metis.domain.protocols import TaskStore
from metis.domain.value_objects import TaskId


@dataclass(frozen=True)
class CancelTaskInput:
    task_id: str
    # Authorization context — when provided, the cancel is rejected if the
    # task's session_id does not match. Leave None to skip the check (which
    # is the correct behaviour for single-tenant deployments and for callers
    # who have already authorized out-of-band).
    session_id: str | None = None


class CancelTaskUseCase:
    """Transitions a non-terminal task to CANCELLED.

    Valid source states: PENDING, CLAIMED, INPUT_REQUIRED.
    Terminal tasks (COMPLETE/CONSUMED/EXPIRED/FAILED/CANCELLED) are rejected
    with TaskAlreadyTerminalError per spec rule that terminal states
    must never transition.

    Per the MCP async-tasks spec §Security: when an authorization context
    is provided, the receiver MUST bind tasks to it. This use case enforces
    that by rejecting cancel attempts whose ``session_id`` does not match
    the task's ``session_id`` — the caller sees a TaskNotFoundError so
    cross-session task IDs are not enumerable.
    """

    def __init__(self, task_store: TaskStore) -> None:
        self._task_store = task_store

    async def execute(self, input: CancelTaskInput) -> Result[None]:
        task_id = TaskId(value=input.task_id)
        task = await self._task_store.get(task_id)

        if task is None:
            return Err(TaskNotFoundError(message=f"Task {input.task_id} not found"))

        # Authorization check — if caller supplied a session_id, require match.
        # Return TaskNotFoundError rather than a permission error so the caller
        # cannot enumerate tasks that belong to other sessions.
        if input.session_id is not None and task.session_id != input.session_id:
            return Err(TaskNotFoundError(message=f"Task {input.task_id} not found"))

        if task.status.is_terminal:
            return Err(
                TaskAlreadyTerminalError(
                    message=(
                        f"Task {input.task_id} is already in terminal status "
                        f"{task.status.value!r}; cancel rejected"
                    )
                )
            )

        # COMPLETE is not terminal per is_terminal but should still be rejected —
        # COMPLETE only transitions to CONSUMED, not CANCELLED.
        try:
            task.cancel()
        except ValueError as e:
            return Err(TaskAlreadyTerminalError(message=str(e)))

        await self._task_store.update(task)
        return Ok(None)
