from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sandbox.helpdesk.domain.sla import (
    InvalidTransition,
    Priority,
    TicketStatus,
    due_at,
    escalation_level,
    is_breached,
    transition,
)

CREATED = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_due_at_by_priority() -> None:
    assert due_at(CREATED, Priority.LOW) == CREATED + timedelta(hours=72)
    assert due_at(CREATED, Priority.NORMAL) == CREATED + timedelta(hours=24)
    assert due_at(CREATED, Priority.HIGH) == CREATED + timedelta(hours=4)


def test_is_breached_at_the_boundary() -> None:
    due = due_at(CREATED, Priority.HIGH)
    assert is_breached(due, due - timedelta(seconds=1)) is False
    assert is_breached(due, due) is True
    assert is_breached(due, due + timedelta(seconds=1)) is True


def test_escalation_level_at_the_boundaries() -> None:
    due = due_at(CREATED, Priority.HIGH)
    assert escalation_level(due, due - timedelta(seconds=1)) == 0
    assert escalation_level(due, due) == 1
    assert escalation_level(due, due + timedelta(hours=4) - timedelta(seconds=1)) == 1
    assert escalation_level(due, due + timedelta(hours=4)) == 2


def test_transition_valid_chain() -> None:
    assert transition(TicketStatus.OPEN, TicketStatus.CLAIMED) is TicketStatus.CLAIMED
    assert transition(TicketStatus.CLAIMED, TicketStatus.RESOLVED) is TicketStatus.RESOLVED


def test_transition_invalid_raises() -> None:
    with pytest.raises(InvalidTransition):
        transition(TicketStatus.OPEN, TicketStatus.RESOLVED)
    with pytest.raises(InvalidTransition):
        transition(TicketStatus.RESOLVED, TicketStatus.OPEN)
