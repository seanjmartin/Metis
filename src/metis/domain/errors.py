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
class TaskAlreadyTerminalError(MetisError):
    """Raised when an operation targets a task already in a terminal state."""

    code: str = "TASK_ALREADY_TERMINAL"


@dataclass(frozen=True)
class TaskCancelledError(MetisError):
    """Raised when a task has been cancelled."""

    code: str = "TASK_CANCELLED"


@dataclass(frozen=True)
class TaskFailedError(MetisError):
    """Raised when a task has entered the FAILED state."""

    code: str = "TASK_FAILED"


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


class MetisException(Exception):  # noqa: N818 — "Error" suffix is taken by the MetisError dataclass hierarchy above
    """Exception wrapping a MetisError for the public API boundary.

    The Result pattern is used internally by use cases. When a public
    facade method has to raise (because returning a Result would burden
    every caller), it raises MetisException with the typed MetisError
    attached as `.error` so callers can discriminate with isinstance.

    Example:
        try:
            result = await queue.wait_for_result(task_id, timeout=30)
        except MetisException as e:
            if isinstance(e.error, TaskExpiredError):
                ...
    """

    def __init__(self, error: MetisError) -> None:
        super().__init__(f"{error.code}: {error.message}")
        self.error = error
