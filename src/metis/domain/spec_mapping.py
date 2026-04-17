"""Translate Metis internal TaskStatus to the MCP async-tasks spec vocabulary.

The MCP spec (2025-11-25) defines five task statuses:
working, input_required, completed, failed, cancelled.

Metis has a richer internal model (CONSUMED as a read-receipt; EXPIRED
distinct from FAILED) which this module collapses for presentation to
spec-aware clients.

NOT responsible for:
- Any I/O (pure translation)
- Transition validation (see Task entity)
"""

from __future__ import annotations

from typing import Literal

from metis.domain.value_objects import TaskStatus

SpecStatus = Literal["working", "input_required", "completed", "failed", "cancelled"]


_MAPPING: dict[TaskStatus, SpecStatus] = {
    TaskStatus.PENDING: "working",
    TaskStatus.CLAIMED: "working",
    TaskStatus.INPUT_REQUIRED: "input_required",
    TaskStatus.COMPLETE: "completed",
    TaskStatus.CONSUMED: "completed",
    TaskStatus.EXPIRED: "failed",
    TaskStatus.FAILED: "failed",
    TaskStatus.CANCELLED: "cancelled",
}


def internal_to_spec_status(status: TaskStatus) -> SpecStatus:
    """Map an internal TaskStatus to its spec-compliant representation.

    Every TaskStatus has exactly one spec status; missing mappings are a bug.
    """
    try:
        return _MAPPING[status]
    except KeyError as e:
        raise ValueError(f"No spec mapping for TaskStatus {status!r}") from e
