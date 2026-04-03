"""Domain value objects — immutable, identity-less types.

NOT responsible for:
- Persistence or serialization (see infrastructure layer)
- Business logic beyond self-validation (see entities)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class TaskId:
    """Unique identifier for a task. Wraps a UUID string."""

    value: str

    def __post_init__(self) -> None:
        try:
            uuid.UUID(self.value)
        except ValueError as e:
            raise ValueError(f"TaskId must be a valid UUID, got: {self.value!r}") from e

    @classmethod
    def generate(cls) -> TaskId:
        return cls(value=str(uuid.uuid4()))

    def __str__(self) -> str:
        return self.value


class TaskStatus(StrEnum):
    """Lifecycle status of a task.

    Transitions: PENDING -> CLAIMED -> COMPLETE -> CONSUMED
                 PENDING -> EXPIRED
                 CLAIMED -> EXPIRED
    """

    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETE = "complete"
    CONSUMED = "consumed"
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        return self in (TaskStatus.CONSUMED, TaskStatus.EXPIRED)


@dataclass(frozen=True)
class TaskPriority:
    """Task priority. Higher value = more urgent. Default 0."""

    value: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.value, int):
            raise TypeError(f"TaskPriority must be an int, got: {type(self.value).__name__}")


@dataclass(frozen=True)
class WorkerId:
    """Identifies a dispatcher instance."""

    value: str

    def __post_init__(self) -> None:
        if not self.value or not self.value.strip():
            raise ValueError("WorkerId must be a non-empty string")

    def __str__(self) -> str:
        return self.value
