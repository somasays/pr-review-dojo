"""Tests for sandbox/parking/service.py."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from sandbox.parking.db import Garage
from sandbox.parking.domain.pricing import TicketStatus
from sandbox.parking.service import AlreadyOpen, Full, NotPaid, ParkingService
from sandbox.parking.tests.conftest import CARD, EPOCH


def test_enter_opens_a_ticket_and_counts_toward_capacity(db: Session, garage: Garage) -> None:
    service = ParkingService(db)
    ticket = service.enter(garage.id, "ABC123", EPOCH)
    assert ticket.status == TicketStatus.OPEN.value
    assert service.tickets.open_count(garage.id) == 1


def test_enter_rejects_a_full_garage(db: Session, garage: Garage) -> None:
    service = ParkingService(db)
    service.enter(garage.id, "AAA111", EPOCH)
    service.enter(garage.id, "BBB222", EPOCH)
    with pytest.raises(Full):
        service.enter(garage.id, "CCC333", EPOCH)


def test_enter_rejects_a_second_open_ticket_for_the_same_plate(db: Session, garage: Garage) -> None:
    service = ParkingService(db)
    service.enter(garage.id, "ABC123", EPOCH)
    with pytest.raises(AlreadyOpen):
        service.enter(garage.id, "ABC123", EPOCH)


def test_pay_then_exit_within_the_window_keeps_the_paid_fee(db: Session, garage: Garage) -> None:
    service = ParkingService(db)
    ticket = service.enter(garage.id, "ABC123", EPOCH)
    paid = service.pay(ticket.id, EPOCH + timedelta(minutes=50), CARD)
    assert paid.fee == Decimal("4.00")
    exited = service.exit(ticket.id, paid.paid_at + timedelta(minutes=10), CARD)
    assert exited.status == TicketStatus.EXITED.value
    assert exited.fee == Decimal("4.00")


def test_exit_after_the_15_minute_window_recomputes_the_fee(db: Session, garage: Garage) -> None:
    service = ParkingService(db)
    ticket = service.enter(garage.id, "ABC123", EPOCH)
    paid = service.pay(ticket.id, EPOCH + timedelta(minutes=50), CARD)
    assert paid.fee == Decimal("4.00")
    exited = service.exit(ticket.id, paid.paid_at + timedelta(minutes=20), CARD)
    assert exited.fee == Decimal("6.00")


def test_exit_rejects_a_ticket_that_has_not_been_paid(db: Session, garage: Garage) -> None:
    service = ParkingService(db)
    ticket = service.enter(garage.id, "ABC123", EPOCH)
    with pytest.raises(NotPaid):
        service.exit(ticket.id, EPOCH + timedelta(minutes=5), CARD)
