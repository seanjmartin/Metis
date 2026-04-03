"""Tests for domain value objects — pure logic, no I/O."""

import uuid

import pytest

from metis.domain.value_objects import TaskId, TaskPriority, TaskStatus, WorkerId


class TestTaskId:
    def test_should_accept_valid_uuid(self) -> None:
        valid = str(uuid.uuid4())
        task_id = TaskId(value=valid)
        assert task_id.value == valid

    def test_should_reject_invalid_uuid(self) -> None:
        with pytest.raises(ValueError, match="valid UUID"):
            TaskId(value="not-a-uuid")

    def test_should_reject_empty_string(self) -> None:
        with pytest.raises(ValueError, match="valid UUID"):
            TaskId(value="")

    def test_generate_should_produce_unique_ids(self) -> None:
        ids = {TaskId.generate() for _ in range(100)}
        assert len(ids) == 100

    def test_should_be_frozen(self) -> None:
        task_id = TaskId.generate()
        with pytest.raises(AttributeError):
            task_id.value = "something"  # type: ignore[misc]

    def test_str_should_return_value(self) -> None:
        value = str(uuid.uuid4())
        assert str(TaskId(value=value)) == value


class TestTaskStatus:
    def test_terminal_statuses(self) -> None:
        assert TaskStatus.CONSUMED.is_terminal is True
        assert TaskStatus.EXPIRED.is_terminal is True

    def test_non_terminal_statuses(self) -> None:
        assert TaskStatus.PENDING.is_terminal is False
        assert TaskStatus.CLAIMED.is_terminal is False
        assert TaskStatus.COMPLETE.is_terminal is False

    def test_should_be_string_valued(self) -> None:
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.CLAIMED == "claimed"


class TestTaskPriority:
    def test_default_is_zero(self) -> None:
        assert TaskPriority().value == 0

    def test_should_accept_positive(self) -> None:
        assert TaskPriority(value=10).value == 10

    def test_should_accept_negative(self) -> None:
        assert TaskPriority(value=-1).value == -1

    def test_should_be_frozen(self) -> None:
        p = TaskPriority(value=5)
        with pytest.raises(AttributeError):
            p.value = 10  # type: ignore[misc]


class TestWorkerId:
    def test_should_accept_non_empty_string(self) -> None:
        w = WorkerId(value="dispatcher-1")
        assert w.value == "dispatcher-1"

    def test_should_reject_empty_string(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            WorkerId(value="")

    def test_should_reject_whitespace_only(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            WorkerId(value="   ")

    def test_str_should_return_value(self) -> None:
        assert str(WorkerId(value="w1")) == "w1"
