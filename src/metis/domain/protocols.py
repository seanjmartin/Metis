"""Domain protocols — interfaces for infrastructure implementations.

NOT responsible for:
- Implementation details (see infrastructure layer)
- Business logic (see entities and use cases)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from metis.domain.entities import Heartbeat, Task
from metis.domain.value_objects import TaskId, WorkerId


@dataclass(frozen=True)
class ProgressUpdate:
    """An append-only progress report for a task.

    seq is monotonically increasing per task and lets consumers poll
    only for updates they haven't yet seen.
    """

    task_id: TaskId
    seq: int
    progress: float
    total: float | None
    message: str | None
    created_at: datetime


class TaskStore(Protocol):
    """Repository interface for task persistence.

    NOT responsible for:
    - Task lifecycle logic (see Task entity)
    - Use case orchestration (see application layer)
    """

    async def insert(self, task: Task) -> None: ...

    async def get(self, task_id: TaskId) -> Task | None: ...

    async def claim_next(
        self,
        capabilities: list[str],
        worker_id: WorkerId,
        session_id: str | None = None,
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


class ProgressStore(Protocol):
    """Repository interface for append-only task progress events.

    Progress is forwarded to originating clients via MCP's progressToken
    mechanism. The store is used by the dispatcher to append and by the
    trigger-side wait loop to tail new entries.

    NOT responsible for:
    - Rate-limiting / coalescing (done by the worker tool, not the store)
    - Translating to MCP progress notifications (done by the facade/tool layer)
    """

    async def append(
        self,
        task_id: TaskId,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> int:
        """Append a progress update. Returns the new seq."""
        ...

    async def tail_since(self, task_id: TaskId, last_seq: int) -> list[ProgressUpdate]:
        """Return every update for the task with seq > last_seq, oldest first."""
        ...
