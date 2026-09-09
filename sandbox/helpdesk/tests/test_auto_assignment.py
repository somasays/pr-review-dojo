"""Tests for agent capacity and auto-assignment."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from sandbox.helpdesk.assigner import AutoAssigner
from sandbox.helpdesk.db import Agent
from sandbox.helpdesk.domain.sla import Priority
from sandbox.helpdesk.repo import TicketRepo
from sandbox.helpdesk.service import NotAllowed, TicketService
from sandbox.helpdesk.tests.conftest import LEAD_EMAIL

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_claim_refused_at_capacity(db: Session, session_factory: sessionmaker[Session]) -> None:
    limited = Agent(email="cap@example.com", active=True, capacity=2)
    db.add(limited)
    db.commit()

    service = TicketService(session_factory)
    tickets = [service.create(f"ticket {i}", Priority.LOW, NOW) for i in range(5)]

    successful = 0
    refused = 0
    for ticket in tickets:
        try:
            service.claim(limited.email, ticket.id, NOW)
            successful += 1
        except NotAllowed:
            refused += 1

    assert refused >= 1
    assert successful <= limited.capacity + 1


def test_lead_reassigns_a_claimed_ticket(
    db: Session, seeded: dict[str, Agent], session_factory: sessionmaker[Session]
) -> None:
    service = TicketService(session_factory)
    ticket = service.create("subject", Priority.LOW, NOW)
    service.claim(seeded["alice"].email, ticket.id, NOW)

    reassigned = service.assign(LEAD_EMAIL, ticket.id, seeded["bob"].email, NOW)
    assert reassigned.agent_id == seeded["bob"].id
    assert reassigned.status == "claimed"


def test_auto_assigner_assigns_the_oldest_ticket(
    db: Session, seeded: dict[str, Agent], session_factory: sessionmaker[Session]
) -> None:
    service = TicketService(session_factory)
    older = service.create("older ticket", Priority.LOW, NOW - timedelta(minutes=5))
    service.create("newer ticket", Priority.LOW, NOW)

    assigner = AutoAssigner(session_factory, interval_seconds=0.05)
    assigner.start()
    time.sleep(0.3)
    assigner.stop()
    assert assigner._thread is not None
    assigner._thread.join(timeout=1)

    with session_factory() as check:
        refreshed = TicketRepo(check).get(older.id)
        assert refreshed is not None
        assert refreshed.agent_id is not None
