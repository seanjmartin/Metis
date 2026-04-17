"""Metis — Intelligence-on-demand for MCP servers.

Provides a SQLite-backed task queue that lets MCP servers dispatch
reasoning work to background LLM agents and receive structured results.

NOT responsible for:
- Executing tasks (see dispatcher agent)
- Exposing MCP tools (see metis.presentation.worker_server)
- Managing dispatcher lifecycle (see self-healing protocol)
"""

from metis.domain.entities import Task
from metis.domain.errors import (
    InvalidTransitionError,
    MetisError,
    MetisException,
    NoWorkerError,
    TaskAlreadyTerminalError,
    TaskCancelledError,
    TaskExpiredError,
    TaskFailedError,
    TaskNotFoundError,
)
from metis.domain.value_objects import TaskId, TaskStatus
from metis.infrastructure.task_queue_facade import TaskQueue

__all__ = [
    "InvalidTransitionError",
    "MetisError",
    "MetisException",
    "NoWorkerError",
    "Task",
    "TaskAlreadyTerminalError",
    "TaskCancelledError",
    "TaskExpiredError",
    "TaskFailedError",
    "TaskId",
    "TaskNotFoundError",
    "TaskQueue",
    "TaskStatus",
]
