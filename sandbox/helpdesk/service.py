"""Create, claim, and resolve: business rules layered on the repositories.
Each public method opens exactly one unit of work."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from sandbox.helpdesk.db import Ticket, ensure_aware_utc, unit_of_work
from sandbox.helpdesk.domain.sla import InvalidTransition, Priority, TicketStatus, due_at
from sandbox.helpdesk.domain.sla import transition as transition_status
from sandbox.helpdesk.repo import AgentRepo, TicketRepo


class NotFound(Exception):
    pass


class NotAllowed(Exception):
    pass


class AlreadyClaimed(Exception):
    pass


class TicketService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create(self, subject: str, priority: Priority, now: datetime) -> Ticket:
        """Open a ticket, due time computed from its priority."""
        ensure_aware_utc(now)
        with unit_of_work(self.session_factory) as session:
            ticket = Ticket(
                subject=subject,
                priority=priority.value,
                status=TicketStatus.OPEN.value,
                created_at=now,
                due_at=due_at(now, priority),
            )
            return TicketRepo(session).add(ticket)

    def claim(self, agent_email: str, ticket_id: int, now: datetime) -> Ticket:
        """Raises AlreadyClaimed if the ticket is not open."""
        ensure_aware_utc(now)
        with unit_of_work(self.session_factory) as session:
            agent = AgentRepo(session).by_email(agent_email)
            if agent is None or not agent.active:
                raise NotFound(f"agent {agent_email!r} not found or inactive")

            ticket = TicketRepo(session).get(ticket_id)
            if ticket is None:
                raise NotFound(f"ticket {ticket_id} not found")
            if TicketStatus(ticket.status) is not TicketStatus.OPEN:
                raise AlreadyClaimed(f"ticket {ticket_id} is not open")

            ticket.agent_id = agent.id
            ticket.status = transition_status(TicketStatus.OPEN, TicketStatus.CLAIMED).value
            ticket.claimed_at = now
            session.flush()
            return ticket

    def resolve(
        self, agent_email: str, ticket_id: int, now: datetime, *, is_lead: bool = False
    ) -> Ticket:
        """Only the claiming agent or a lead may resolve a ticket."""
        ensure_aware_utc(now)
        with unit_of_work(self.session_factory) as session:
            ticket = TicketRepo(session).get(ticket_id)
            if ticket is None:
                raise NotFound(f"ticket {ticket_id} not found")

            if not is_lead:
                agent = AgentRepo(session).by_email(agent_email)
                if agent is None or ticket.agent_id != agent.id:
                    raise NotAllowed(f"{agent_email!r} did not claim ticket {ticket_id}")

            try:
                ticket.status = transition_status(
                    TicketStatus(ticket.status), TicketStatus.RESOLVED
                ).value
            except InvalidTransition as exc:
                raise NotAllowed(str(exc)) from exc

            ticket.resolved_at = now
            session.flush()
            return ticket
