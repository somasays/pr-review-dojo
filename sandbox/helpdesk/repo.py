"""Repositories: the only place that builds queries. Bound parameters
only, flush but never commit; the service owns the transaction through
db.unit_of_work()."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sandbox.helpdesk.db import Agent, Ticket
from sandbox.helpdesk.domain.sla import TicketStatus

_OPEN_STATUSES = (TicketStatus.OPEN.value, TicketStatus.CLAIMED.value)


class AgentRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def by_email(self, email: str) -> Agent | None:
        return self.session.scalars(select(Agent).where(Agent.email == email)).first()

    def active(self) -> Sequence[Agent]:
        return self.session.scalars(select(Agent).where(Agent.active.is_(True))).all()


class TicketRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, ticket: Ticket) -> Ticket:
        self.session.add(ticket)
        self.session.flush()
        return ticket

    def get(self, ticket_id: int) -> Ticket | None:
        return self.session.get(Ticket, ticket_id)

    def unassigned_oldest_first(self, limit: int) -> Sequence[Ticket]:
        stmt = (
            select(Ticket)
            .where(Ticket.status == TicketStatus.OPEN.value, Ticket.agent_id.is_(None))
            .order_by(Ticket.created_at.asc())
            .limit(limit)
        )
        return self.session.scalars(stmt).all()

    def open_count_for_agent(self, agent_id: int) -> int:
        stmt = select(func.count()).where(
            Ticket.agent_id == agent_id, Ticket.status == TicketStatus.CLAIMED.value
        )
        return self.session.scalar(stmt) or 0

    def breached(self, now: datetime) -> Sequence[Ticket]:
        stmt = select(Ticket).where(Ticket.status.in_(_OPEN_STATUSES), Ticket.due_at <= now)
        return self.session.scalars(stmt).all()
