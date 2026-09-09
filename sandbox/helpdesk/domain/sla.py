"""SLA math: pure logic with no IO. See sandbox/helpdesk/README.md for the
conventions this module follows."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

_DUE_HOURS = {"low": 72, "normal": 24, "high": 4}
_ESCALATION_WINDOW = timedelta(hours=4)


class Priority(Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


def due_at(created_at: datetime, priority: Priority) -> datetime:
    """72 hours for low, 24 for normal, 4 for high."""
    return created_at + timedelta(hours=_DUE_HOURS[priority.value])


def is_breached(due_at: datetime, now: datetime) -> bool:
    """True once `now` reaches the due time."""
    return now >= due_at


def escalation_level(due_at: datetime, now: datetime) -> int:
    """0 before due, 1 within 4 hours past due, 2 beyond that."""
    if now < due_at:
        return 0
    if now < due_at + _ESCALATION_WINDOW:
        return 1
    return 2


class TicketStatus(Enum):
    OPEN = "open"
    CLAIMED = "claimed"
    RESOLVED = "resolved"


class InvalidTransition(Exception):
    pass


_ALLOWED = {
    TicketStatus.OPEN: TicketStatus.CLAIMED,
    TicketStatus.CLAIMED: TicketStatus.RESOLVED,
}


def transition(current: TicketStatus, target: TicketStatus) -> TicketStatus:
    """The only function allowed to move a ticket's status. Raises
    InvalidTransition for any move other than open -> claimed -> resolved."""
    if _ALLOWED.get(current) is not target:
        raise InvalidTransition(f"cannot move a ticket from {current.value} to {target.value}")
    return target
