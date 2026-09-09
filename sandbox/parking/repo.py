"""Repositories: the only place that builds queries.

Convention (see sandbox/parking/README.md): bound parameters only, flush
but never commit. The API dependency owns the transaction through
db.session_scope().
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sandbox.parking.db import Garage, Ticket
from sandbox.parking.domain.pricing import TicketStatus


class GarageRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, garage_id: int) -> Garage | None:
        return self.session.get(Garage, garage_id)


class TicketRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, ticket: Ticket) -> Ticket:
        self.session.add(ticket)
        self.session.flush()
        return ticket

    def get(self, ticket_id: int) -> Ticket | None:
        return self.session.get(Ticket, ticket_id)

    def open_for_plate(self, garage_id: int, plate: str) -> Ticket | None:
        stmt = select(Ticket).where(
            Ticket.garage_id == garage_id,
            Ticket.plate == plate,
            Ticket.status == TicketStatus.OPEN.value,
        )
        return self.session.scalars(stmt).first()

    def open_count(self, garage_id: int) -> int:
        stmt = select(func.count()).where(
            Ticket.garage_id == garage_id, Ticket.status == TicketStatus.OPEN.value
        )
        return self.session.scalar(stmt) or 0
