from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from sandbox.helpdesk.db import Agent
from sandbox.helpdesk.domain.sla import Priority, TicketStatus, due_at
from sandbox.helpdesk.service import AlreadyClaimed, NotAllowed, TicketService
from sandbox.helpdesk.tests.conftest import AGENT_EMAIL, LEAD_EMAIL, OTHER_AGENT_EMAIL

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_create_ticket_sets_due_at(session_factory: sessionmaker[Session], seeded) -> None:
    service = TicketService(session_factory)
    ticket = service.create("printer is on fire", Priority.HIGH, NOW)
    assert ticket.status == TicketStatus.OPEN.value
    assert ticket.due_at == due_at(NOW, Priority.HIGH)


def test_claim_then_already_claimed(session_factory: sessionmaker[Session], seeded) -> None:
    service = TicketService(session_factory)
    ticket = service.create("printer is on fire", Priority.HIGH, NOW)

    claimed = service.claim(AGENT_EMAIL, ticket.id, NOW)
    assert claimed.status == TicketStatus.CLAIMED.value
    assert claimed.agent_id == seeded["alice"].id

    with pytest.raises(AlreadyClaimed):
        service.claim(OTHER_AGENT_EMAIL, ticket.id, NOW)


def test_resolve_by_claiming_agent_and_lead(
    session_factory: sessionmaker[Session], seeded: dict[str, Agent]
) -> None:
    service = TicketService(session_factory)
    claimed_one = service.create("subject one", Priority.LOW, NOW)
    service.claim(AGENT_EMAIL, claimed_one.id, NOW)
    resolved_by_agent = service.resolve(AGENT_EMAIL, claimed_one.id, NOW)
    assert resolved_by_agent.status == TicketStatus.RESOLVED.value

    claimed_two = service.create("subject two", Priority.LOW, NOW)
    service.claim(AGENT_EMAIL, claimed_two.id, NOW)
    resolved_by_lead = service.resolve(LEAD_EMAIL, claimed_two.id, NOW, is_lead=True)
    assert resolved_by_lead.status == TicketStatus.RESOLVED.value


def test_resolve_by_unrelated_agent_raises_not_allowed(
    session_factory: sessionmaker[Session], seeded
) -> None:
    service = TicketService(session_factory)
    ticket = service.create("subject", Priority.LOW, NOW)
    service.claim(AGENT_EMAIL, ticket.id, NOW)

    with pytest.raises(NotAllowed):
        service.resolve(OTHER_AGENT_EMAIL, ticket.id, NOW)
