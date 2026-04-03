"""Domain error types and Result monad.

NOT responsible for:
- HTTP/MCP error formatting (see presentation layer)
- Logging or telemetry (see infrastructure layer)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class MetisError:
    """Base error type for domain-level failures."""

    message: str
    code: str


@dataclass(frozen=True)
class TaskNotFoundError(MetisError):
    """Raised when a task cannot be found by ID."""

    code: str = "TASK_NOT_FOUND"


@dataclass(frozen=True)
class TaskExpiredError(MetisError):
    """Raised when a task has exceeded its TTL."""

    code: str = "TASK_EXPIRED"


@dataclass(frozen=True)
class InvalidTransitionError(MetisError):
    """Raised when a task status transition is not allowed."""

    code: str = "INVALID_TRANSITION"


@dataclass(frozen=True)
class NoWorkerError(MetisError):
    """Raised when no dispatcher worker is alive."""

    code: str = "NO_WORKER"


@dataclass(frozen=True)
class Ok(Generic[T]):
    """Successful result wrapping a value."""

    value: T

    @property
    def is_ok(self) -> bool:
        return True

    @property
    def is_error(self) -> bool:
        return False


@dataclass(frozen=True)
class Err:
    """Failed result wrapping a MetisError."""

    error: MetisError

    @property
    def is_ok(self) -> bool:
        return False

    @property
    def is_error(self) -> bool:
        return True


Result = Ok[T] | Err
