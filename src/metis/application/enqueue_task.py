"""Use case: enqueue a new task for background processing.

NOT responsible for:
- Task execution (see dispatcher agent)
- Database connection management (see infrastructure layer)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from metis.domain.entities import Task
from metis.domain.errors import Ok, Result
from metis.domain.protocols import TaskStore
from metis.domain.value_objects import TaskId, TaskPriority


@dataclass(frozen=True)
class EnqueueTaskInput:
    type: str
    payload: dict[str, Any]
    priority: int = 0
    ttl_seconds: int = 300
    capabilities_required: list[str] = field(default_factory=list)
    session_id: str | None = None


class EnqueueTaskUseCase:
    """Creates a new task and inserts it into the store.

    NOT responsible for:
    - Waiting for results (see WaitForResultUseCase)
    - Checking dispatcher health (see CheckHealthUseCase)
    """

    def __init__(self, task_store: TaskStore) -> None:
        self._task_store = task_store

    async def execute(self, input: EnqueueTaskInput) -> Result[TaskId]:
        task = Task(
            id=TaskId.generate(),
            type=input.type,
            payload=input.payload,
            priority=TaskPriority(value=input.priority),
            ttl_seconds=input.ttl_seconds,
            capabilities_required=input.capabilities_required,
            session_id=input.session_id,
            created_at=datetime.now(UTC),
        )
        await self._task_store.insert(task)
        return Ok(task.id)
