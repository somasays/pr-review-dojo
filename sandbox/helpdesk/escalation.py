"""Escalation job: for every breached open or claimed ticket, notify once
per escalation level change, tracked by Ticket.escalation_level."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from sandbox.helpdesk.db import Ticket, coerce_utc, unit_of_work
from sandbox.helpdesk.domain.sla import escalation_level
from sandbox.helpdesk.repo import TicketRepo

Notifier = Callable[[Ticket, int], None]


def escalate_breached(
    session_factory: sessionmaker[Session], now: datetime, notifier: Notifier
) -> None:
    with unit_of_work(session_factory) as session:
        for ticket in TicketRepo(session).breached(now):
            level = escalation_level(coerce_utc(ticket.due_at), now)
            if level > ticket.escalation_level:
                notifier(ticket, level)
                ticket.escalation_level = level
        session.flush()
