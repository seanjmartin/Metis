"""Tests for internal-to-spec status mapping."""

from __future__ import annotations

import pytest

from metis.domain.spec_mapping import internal_to_spec_status
from metis.domain.value_objects import TaskStatus


class TestInternalToSpec:
    @pytest.mark.parametrize(
        ("internal", "spec"),
        [
            (TaskStatus.PENDING, "working"),
            (TaskStatus.CLAIMED, "working"),
            (TaskStatus.INPUT_REQUIRED, "input_required"),
            (TaskStatus.COMPLETE, "completed"),
            (TaskStatus.CONSUMED, "completed"),
            (TaskStatus.EXPIRED, "failed"),
            (TaskStatus.FAILED, "failed"),
            (TaskStatus.CANCELLED, "cancelled"),
        ],
    )
    def test_maps_every_internal_status(self, internal: TaskStatus, spec: str) -> None:
        assert internal_to_spec_status(internal) == spec

    def test_all_statuses_have_a_mapping(self) -> None:
        for status in TaskStatus:
            internal_to_spec_status(status)  # must not raise
