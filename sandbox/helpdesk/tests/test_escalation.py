from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from sandbox.helpdesk.db import Ticket
from sandbox.helpdesk.domain.sla import Priority, TicketStatus, due_at
from sandbox.helpdesk.escalation import escalate_breached
from sandbox.helpdesk.repo import TicketRepo

CREATED = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def test_escalate_once_per_level(
    session_factory: sessionmaker[Session], db: Session, seeded
) -> None:
    ticket = TicketRepo(db).add(
        Ticket(
            subject="overdue",
            priority=Priority.HIGH.value,
            status=TicketStatus.OPEN.value,
            created_at=CREATED,
            due_at=due_at(CREATED, Priority.HIGH),
        )
    )
    db.commit()
    ticket_id = ticket.id

    calls: list[tuple[int, int]] = []

    def notifier(t: Ticket, level: int) -> None:
        calls.append((t.id, level))

    due = due_at(CREATED, Priority.HIGH)

    escalate_breached(session_factory, due, notifier)
    escalate_breached(session_factory, due, notifier)
    assert calls == [(ticket_id, 1)]

    escalate_breached(session_factory, due + timedelta(hours=4), notifier)
    assert calls == [(ticket_id, 1), (ticket_id, 2)]
