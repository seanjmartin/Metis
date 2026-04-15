"""Tests for Task entity — pure logic, no I/O."""

from datetime import UTC, datetime, timedelta

import pytest

from metis.domain.entities import Heartbeat, Task
from metis.domain.value_objects import TaskId, TaskPriority, TaskStatus, WorkerId


def _make_task(
    *,
    status: TaskStatus = TaskStatus.PENDING,
    ttl_seconds: int = 300,
    created_at: datetime | None = None,
) -> Task:
    """Test builder for Task entities."""
    return Task(
        id=TaskId.generate(),
        type="test",
        payload={"key": "value"},
        status=status,
        priority=TaskPriority(value=0),
        ttl_seconds=ttl_seconds,
        created_at=created_at or datetime.now(UTC),
    )


class TestTaskCreation:
    def test_should_default_to_pending(self) -> None:
        task = _make_task()
        assert task.status == TaskStatus.PENDING

    def test_should_have_no_result_initially(self) -> None:
        task = _make_task()
        assert task.result is None
        assert task.claimed_at is None
        assert task.completed_at is None


class TestTaskTransitions:
    def test_should_claim_from_pending(self) -> None:
        task = _make_task()
        task.claim(WorkerId(value="w1"))
        assert task.status == TaskStatus.CLAIMED
        assert task.claimed_at is not None

    def test_should_complete_from_claimed(self) -> None:
        task = _make_task()
        task.claim(WorkerId(value="w1"))
        task.complete({"answer": 42})
        assert task.status == TaskStatus.COMPLETE
        assert task.result == {"answer": 42}
        assert task.completed_at is not None

    def test_should_consume_from_complete(self) -> None:
        task = _make_task()
        task.claim(WorkerId(value="w1"))
        task.complete({"answer": 42})
        task.consume()
        assert task.status == TaskStatus.CONSUMED

    def test_should_expire_from_pending(self) -> None:
        task = _make_task()
        task.expire()
        assert task.status == TaskStatus.EXPIRED

    def test_should_expire_from_claimed(self) -> None:
        task = _make_task()
        task.claim(WorkerId(value="w1"))
        task.expire()
        assert task.status == TaskStatus.EXPIRED

    def test_should_reject_claim_from_complete(self) -> None:
        task = _make_task()
        task.claim(WorkerId(value="w1"))
        task.complete({"x": 1})
        with pytest.raises(ValueError, match="Cannot transition"):
            task.claim(WorkerId(value="w2"))

    def test_should_reject_complete_from_pending(self) -> None:
        task = _make_task()
        with pytest.raises(ValueError, match="Cannot transition"):
            task.complete({"x": 1})

    def test_should_reject_consume_from_pending(self) -> None:
        task = _make_task()
        with pytest.raises(ValueError, match="Cannot transition"):
            task.consume()

    def test_should_reject_transition_from_expired(self) -> None:
        task = _make_task()
        task.expire()
        with pytest.raises(ValueError, match="Cannot transition"):
            task.claim(WorkerId(value="w1"))

    def test_should_reject_transition_from_consumed(self) -> None:
        task = _make_task()
        task.claim(WorkerId(value="w1"))
        task.complete({"x": 1})
        task.consume()
        with pytest.raises(ValueError, match="Cannot transition"):
            task.expire()


class TestTaskExpiry:
    def test_should_not_be_expired_when_fresh(self) -> None:
        task = _make_task(ttl_seconds=300)
        assert task.is_expired() is False

    def test_should_be_expired_when_past_ttl(self) -> None:
        task = _make_task(
            ttl_seconds=10,
            created_at=datetime.now(UTC) - timedelta(seconds=20),
        )
        assert task.is_expired() is True

    def test_should_be_expired_at_exact_ttl(self) -> None:
        task = _make_task(
            ttl_seconds=0,
            created_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        assert task.is_expired() is True


class TestTaskValidation:
    def test_should_reject_empty_type(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            _make_task()  # uses type="test" by default — override manually
            Task(
                id=TaskId.generate(),
                type="",
                payload={},
            )

    def test_should_reject_whitespace_type(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            Task(
                id=TaskId.generate(),
                type="   ",
                payload={},
            )

    def test_should_reject_negative_ttl(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            Task(
                id=TaskId.generate(),
                type="test",
                payload={},
                ttl_seconds=-1,
            )

    def test_should_accept_zero_ttl(self) -> None:
        task = Task(
            id=TaskId.generate(),
            type="test",
            payload={},
            ttl_seconds=0,
        )
        assert task.ttl_seconds == 0


class TestSessionId:
    def test_should_default_to_none(self) -> None:
        task = _make_task()
        assert task.session_id is None

    def test_should_accept_session_id(self) -> None:
        task = Task(
            id=TaskId.generate(),
            type="test",
            payload={},
            session_id="user-alice::session-1",
        )
        assert task.session_id == "user-alice::session-1"


class TestHeartbeat:
    def test_should_be_alive_when_recent(self) -> None:
        hb = Heartbeat(
            worker_id=WorkerId(value="w1"),
            capabilities=["browse-as-me"],
            last_seen=datetime.now(UTC),
        )
        assert hb.is_alive(timeout_seconds=60) is True

    def test_should_be_dead_when_stale(self) -> None:
        hb = Heartbeat(
            worker_id=WorkerId(value="w1"),
            capabilities=[],
            last_seen=datetime.now(UTC) - timedelta(seconds=120),
        )
        assert hb.is_alive(timeout_seconds=60) is False
