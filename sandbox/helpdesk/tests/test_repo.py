from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from sandbox.helpdesk.db import Ticket
from sandbox.helpdesk.domain.sla import Priority, TicketStatus, due_at
from sandbox.helpdesk.repo import AgentRepo, TicketRepo
from sandbox.helpdesk.tests.conftest import AGENT_EMAIL

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_queue_unassigned_oldest_first(db: Session, seeded) -> None:
    tickets = TicketRepo(db)
    older = tickets.add(
        Ticket(
            subject="older",
            priority=Priority.LOW.value,
            status=TicketStatus.OPEN.value,
            created_at=NOW - timedelta(hours=1),
            due_at=due_at(NOW, Priority.LOW),
        )
    )
    newer = tickets.add(
        Ticket(
            subject="newer",
            priority=Priority.LOW.value,
            status=TicketStatus.OPEN.value,
            created_at=NOW,
            due_at=due_at(NOW, Priority.LOW),
        )
    )
    db.commit()

    queue = tickets.unassigned_oldest_first(limit=10)
    assert [t.id for t in queue] == [older.id, newer.id]


def test_open_count_for_agent_counts_only_claimed(db: Session, seeded) -> None:
    agent = seeded["alice"]
    tickets = TicketRepo(db)
    tickets.add(
        Ticket(
            subject="claimed",
            priority=Priority.LOW.value,
            status=TicketStatus.CLAIMED.value,
            created_at=NOW,
            due_at=due_at(NOW, Priority.LOW),
            agent_id=agent.id,
        )
    )
    tickets.add(
        Ticket(
            subject="resolved",
            priority=Priority.LOW.value,
            status=TicketStatus.RESOLVED.value,
            created_at=NOW,
            due_at=due_at(NOW, Priority.LOW),
            agent_id=agent.id,
        )
    )
    db.commit()

    assert tickets.open_count_for_agent(agent.id) == 1


def test_agent_repo_by_email_and_active(db: Session, seeded) -> None:
    agents = AgentRepo(db)
    assert agents.by_email(AGENT_EMAIL) is not None
    assert agents.by_email("nobody@example.com") is None
    assert {a.email for a in agents.active()} == {"alice@example.com", "bob@example.com"}
