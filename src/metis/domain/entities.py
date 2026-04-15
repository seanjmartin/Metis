"""Domain entities — objects with identity and lifecycle.

NOT responsible for:
- Persistence (see TaskStore protocol and SqliteTaskStore)
- Serialization to/from SQL rows (see infrastructure layer)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from metis.domain.value_objects import TaskId, TaskPriority, TaskStatus, WorkerId


@dataclass
class Task:
    """A unit of reasoning work dispatched to a background agent.

    NOT responsible for:
    - Queue management or ordering (see TaskStore.claim_next)
    - Result delivery coordination (see DeliverResultUseCase)
    """

    id: TaskId
    type: str
    payload: dict[str, Any]
    status: TaskStatus = TaskStatus.PENDING
    result: dict[str, Any] | None = None
    priority: TaskPriority = field(default_factory=TaskPriority)
    ttl_seconds: int = 300
    capabilities_required: list[str] = field(default_factory=list)
    session_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    claimed_at: datetime | None = None
    completed_at: datetime | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.type or not self.type.strip():
            raise ValueError("Task type must be a non-empty string")
        if self.ttl_seconds < 0:
            raise ValueError(f"ttl_seconds must be non-negative, got: {self.ttl_seconds}")

    _VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = field(
        default_factory=lambda: {
            TaskStatus.PENDING: {TaskStatus.CLAIMED, TaskStatus.EXPIRED},
            TaskStatus.CLAIMED: {TaskStatus.COMPLETE, TaskStatus.EXPIRED},
            TaskStatus.COMPLETE: {TaskStatus.CONSUMED},
            TaskStatus.CONSUMED: set(),
            TaskStatus.EXPIRED: set(),
        },
        init=False,
        repr=False,
    )

    def is_expired(self) -> bool:
        """Check whether this task has exceeded its TTL."""
        deadline = self.created_at + timedelta(seconds=self.ttl_seconds)
        return datetime.now(UTC) >= deadline

    def claim(self, worker_id: WorkerId) -> None:
        """Transition from PENDING to CLAIMED by a specific worker."""
        self._transition_to(TaskStatus.CLAIMED)
        self.claimed_at = datetime.now(UTC)

    def complete(self, result: dict[str, Any]) -> None:
        """Transition from CLAIMED to COMPLETE with a result payload."""
        self._transition_to(TaskStatus.COMPLETE)
        self.result = result
        self.completed_at = datetime.now(UTC)

    def consume(self) -> None:
        """Transition from COMPLETE to CONSUMED (result has been read)."""
        self._transition_to(TaskStatus.CONSUMED)

    def expire(self) -> None:
        """Transition from PENDING or CLAIMED to EXPIRED."""
        self._transition_to(TaskStatus.EXPIRED)

    def _transition_to(self, new_status: TaskStatus) -> None:
        allowed = self._VALID_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Cannot transition from {self.status.value!r} to {new_status.value!r}"
            )
        self.status = new_status


@dataclass
class Heartbeat:
    """Records that a dispatcher worker is alive.

    NOT responsible for:
    - Deciding what to do when a worker is dead (see CheckHealthUseCase)
    - Persisting heartbeat data (see HeartbeatStore)
    """

    worker_id: WorkerId
    capabilities: list[str]
    last_seen: datetime

    def is_alive(self, timeout_seconds: int = 60) -> bool:
        """Check whether this heartbeat is recent enough to be considered alive."""
        deadline = self.last_seen + timedelta(seconds=timeout_seconds)
        return datetime.now(UTC) < deadline
