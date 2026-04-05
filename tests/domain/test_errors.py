"""Tests for domain error types and Result monad."""

from __future__ import annotations

import pytest

from metis.domain.errors import (
    Err,
    InvalidTransitionError,
    MetisError,
    NoWorkerError,
    Ok,
    TaskExpiredError,
    TaskNotFoundError,
)


class TestOk:
    def test_is_ok_should_be_true(self) -> None:
        result = Ok(value=42)
        assert result.is_ok is True

    def test_is_error_should_be_false(self) -> None:
        result = Ok(value=42)
        assert result.is_error is False

    def test_should_hold_value(self) -> None:
        result = Ok(value={"key": "val"})
        assert result.value == {"key": "val"}

    def test_should_hold_none_value(self) -> None:
        result = Ok(value=None)
        assert result.is_ok is True
        assert result.value is None


class TestErr:
    def test_is_ok_should_be_false(self) -> None:
        result = Err(error=TaskNotFoundError(message="not found"))
        assert result.is_ok is False

    def test_is_error_should_be_true(self) -> None:
        result = Err(error=TaskNotFoundError(message="not found"))
        assert result.is_error is True

    def test_should_hold_error(self) -> None:
        error = TaskExpiredError(message="expired")
        result = Err(error=error)
        assert result.error is error


class TestErrorTypes:
    def test_task_not_found_should_have_correct_code(self) -> None:
        error = TaskNotFoundError(message="missing")
        assert error.code == "TASK_NOT_FOUND"
        assert error.message == "missing"

    def test_task_expired_should_have_correct_code(self) -> None:
        error = TaskExpiredError(message="old")
        assert error.code == "TASK_EXPIRED"

    def test_invalid_transition_should_have_correct_code(self) -> None:
        error = InvalidTransitionError(message="bad")
        assert error.code == "INVALID_TRANSITION"

    def test_no_worker_should_have_correct_code(self) -> None:
        error = NoWorkerError(message="dead")
        assert error.code == "NO_WORKER"

    def test_all_errors_should_be_metis_errors(self) -> None:
        errors = [
            TaskNotFoundError(message="a"),
            TaskExpiredError(message="b"),
            InvalidTransitionError(message="c"),
            NoWorkerError(message="d"),
        ]
        for error in errors:
            assert isinstance(error, MetisError)

    def test_errors_should_be_frozen(self) -> None:
        error = TaskNotFoundError(message="frozen")
        with pytest.raises(AttributeError):
            error.message = "changed"  # type: ignore[misc]
