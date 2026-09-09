"""Enter, pay, and exit: business rules layered on the repositories. Each
method takes a `Session` opened elsewhere and never commits it; the caller
owns the transaction through `db.session_scope()` (see
sandbox/parking/README.md)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from sandbox.parking.db import Pass, Ticket, coerce_utc, ensure_aware_utc
from sandbox.parking.domain.pricing import (
    RateCard,
    TicketStatus,
    billable_minutes,
    fee_for,
    pass_price,
)
from sandbox.parking.domain.pricing import transition as transition_status
from sandbox.parking.repo import GarageRepo, PassRepo, TicketRepo

PAID_GRACE = timedelta(minutes=15)
_DEFAULT_MONTHLY_CENTS = 5000


class NotFound(Exception):
    pass


class Full(Exception):
    pass


class AlreadyOpen(Exception):
    pass


class NotPaid(Exception):
    pass


class OverlappingPass(Exception):
    pass


class ParkingService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.garages = GarageRepo(session)
        self.tickets = TicketRepo(session)
        self.passes = PassRepo(session)

    def enter(self, garage_id: int, plate: str, now: datetime) -> Ticket:
        """Open a ticket for `plate` in `garage_id`, rejecting a full
        garage or a plate that already has an open ticket there."""
        ensure_aware_utc(now)

        garage = self.garages.get(garage_id)
        if garage is None:
            raise NotFound(f"garage {garage_id} not found")
        if self.tickets.open_count(garage_id) >= garage.capacity:
            raise Full(f"garage {garage_id} is full")
        if self.tickets.open_for_plate(garage_id, plate) is not None:
            raise AlreadyOpen(f"plate {plate!r} already has an open ticket in garage {garage_id}")

        ticket = Ticket(
            garage_id=garage_id, plate=plate, entered_at=now, status=TicketStatus.OPEN.value
        )
        return self.tickets.add(ticket)

    def pay(self, ticket_id: int, now: datetime, card: RateCard) -> Ticket:
        """Compute the fee, transition the ticket to paid, and record
        `paid_at`. Free if the plate has an active pass for this garage."""
        ensure_aware_utc(now)

        ticket = self.tickets.get(ticket_id)
        if ticket is None:
            raise NotFound(f"ticket {ticket_id} not found")

        active_pass = self.passes.active_for_plate(ticket.garage_id, ticket.plate, now)
        if active_pass is not None:
            minutes = billable_minutes(coerce_utc(ticket.entered_at), now)
            ticket.fee = Decimal("0.00")
        else:
            minutes = billable_minutes(coerce_utc(ticket.entered_at), now)
            ticket.fee = fee_for(minutes, card)
            ticket.status = transition_status(TicketStatus(ticket.status), TicketStatus.PAID).value

        ticket.paid_at = now
        self.session.flush()
        return ticket

    def buy_pass(
        self, garage_id: int, plate: str, months: int, starts_at: datetime, notify: bool = False
    ) -> Pass:
        """Sell a `months`-month pass for `plate` in `garage_id`, starting
        `starts_at`. Only one pass may be active for a plate at a time."""
        ensure_aware_utc(starts_at)

        existing = self.passes.active_for_plate(garage_id, plate, starts_at)
        if existing is not None:
            raise OverlappingPass(
                f"plate {plate!r} already has an active pass in garage {garage_id}"
            )

        valid_to = starts_at + timedelta(days=30 * months)
        price = pass_price(months, _DEFAULT_MONTHLY_CENTS)
        pass_ = Pass(
            garage_id=garage_id, plate=plate, valid_from=starts_at, valid_to=valid_to, price=price
        )
        return self.passes.add(pass_)

    def exit(self, ticket_id: int, now: datetime, card: RateCard) -> Ticket:
        """Close a paid ticket. Within 15 minutes of `paid_at` the fee
        stands; past that window, the fee is recomputed for the full stay
        so the overstay is charged too."""
        ensure_aware_utc(now)

        ticket = self.tickets.get(ticket_id)
        if ticket is None:
            raise NotFound(f"ticket {ticket_id} not found")
        if TicketStatus(ticket.status) is not TicketStatus.PAID:
            raise NotPaid(f"ticket {ticket_id} has not been paid")

        paid_at = coerce_utc(ticket.paid_at) if ticket.paid_at is not None else None
        if paid_at is not None and now - paid_at > PAID_GRACE:
            minutes = billable_minutes(coerce_utc(ticket.entered_at), now)
            ticket.fee = fee_for(minutes, card)

        ticket.status = transition_status(TicketStatus.PAID, TicketStatus.EXITED).value
        ticket.exited_at = now
        self.session.flush()
        return ticket
