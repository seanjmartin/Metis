"""Use case: append a progress update for a task.

NOT responsible for:
- Forwarding to MCP clients (see the drain loop in wait_for_result)
- Rate-limiting (coalesce in the worker tool, not here)
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
from metis.domain.protocols import ProgressStore, TaskStore
from metis.domain.value_objects import TaskId


@dataclass(frozen=True)
class ReportProgressInput:
    task_id: str
    progress: float
    total: float | None = None
    message: str | None = None


class ReportProgressUseCase:
    """Dispatcher-facing: record progress for an in-flight task.

    Rejects progress reports for terminal tasks (spec invariant: terminal
    states must never mutate further).
    """

    def __init__(
        self,
        task_store: TaskStore,
        progress_store: ProgressStore,
    ) -> None:
        self._task_store = task_store
        self._progress_store = progress_store

    async def execute(self, input: ReportProgressInput) -> Result[int]:
        task_id = TaskId(value=input.task_id)
        task = await self._task_store.get(task_id)

        if task is None:
            return Err(TaskNotFoundError(message=f"Task {input.task_id} not found"))

        if task.status.is_terminal:
            return Err(
                TaskAlreadyTerminalError(
                    message=(
                        f"Task {input.task_id} is terminal ({task.status.value!r}); "
                        "progress rejected"
                    )
                )
            )

        seq = await self._progress_store.append(
            task_id=task_id,
            progress=input.progress,
            total=input.total,
            message=input.message,
        )
        return Ok(seq)
