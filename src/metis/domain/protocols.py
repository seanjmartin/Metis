"""Domain protocols — interfaces for infrastructure implementations.

NOT responsible for:
- Implementation details (see infrastructure layer)
- Business logic (see entities and use cases)
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from metis.domain.entities import Heartbeat, Task
from metis.domain.value_objects import TaskId, WorkerId


class TaskStore(Protocol):
    """Repository interface for task persistence.

    NOT responsible for:
    - Task lifecycle logic (see Task entity)
    - Use case orchestration (see application layer)
    """

    async def insert(self, task: Task) -> None: ...

    async def get(self, task_id: TaskId) -> Task | None: ...

    async def claim_next(
        self, capabilities: list[str], worker_id: WorkerId
    ) -> Task | None: ...

    async def update(self, task: Task) -> None: ...

    async def mark_consumed(self, task_id: TaskId) -> None: ...

    async def expire_stale(self, now: datetime) -> int: ...


class HeartbeatStore(Protocol):
    """Repository interface for heartbeat persistence.

    NOT responsible for:
    - Health decision logic (see CheckHealthUseCase)
    - Worker lifecycle management (see dispatcher agent)
    """

    async def upsert(self, heartbeat: Heartbeat) -> None: ...

    async def get(self, worker_id: WorkerId) -> Heartbeat | None: ...

    async def get_latest(self) -> Heartbeat | None: ...

    async def remove(self, worker_id: WorkerId) -> None: ...
