"""Hidden tests for exercise 51: monthly passes.

Each test below fails against ex/51-monthly-passes and passes against
solutions/51.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session, sessionmaker

from sandbox.parking.db import Garage
from sandbox.parking.domain.pricing import TicketStatus
from sandbox.parking.service import OverlappingPass, ParkingService
from sandbox.parking.tests.conftest import ATTENDANT_KEY, CARD, EPOCH

MONTHLY_CENTS = 5000

HEADERS = {"X-Attendant-Key": ATTENDANT_KEY}


# --- Service: paying while a pass covers the ticket sets the fee but ------
# --- never transitions the ticket to paid, so a pass holder can never -----
# --- exit. -------------------------------------------------------------


def test_paying_with_a_pass_still_lets_the_ticket_exit(db: Session, garage: Garage) -> None:
    service = ParkingService(db)
    service.buy_pass(garage.id, "ABC123", 1, EPOCH, MONTHLY_CENTS)
    ticket = service.enter(garage.id, "ABC123", EPOCH + timedelta(days=1))
    paid = service.pay(ticket.id, EPOCH + timedelta(days=1, minutes=10), CARD)
    assert paid.status == TicketStatus.PAID.value

    exited = service.exit(ticket.id, paid.paid_at + timedelta(minutes=5), CARD)
    assert exited.status == TicketStatus.EXITED.value


# --- Service: the overlap check only looks at whether starts_at falls -----
# --- inside an existing pass, so a new pass that starts before an --------
# --- existing one and fully contains it is wrongly accepted. --------------


def test_overlapping_pass_that_fully_contains_existing_is_rejected(
    db: Session, garage: Garage
) -> None:
    service = ParkingService(db)
    service.buy_pass(garage.id, "ABC123", 1, EPOCH + timedelta(days=5), MONTHLY_CENTS)

    with pytest.raises(OverlappingPass):
        service.buy_pass(garage.id, "ABC123", 2, EPOCH, MONTHLY_CENTS)


# --- Domain: pass_covers treats the window as closed, so a ticket paid ----
# --- exactly at valid_to is wrongly treated as covered by the pass. -------


def test_pass_does_not_cover_a_payment_made_exactly_at_valid_to(
    db: Session, garage: Garage
) -> None:
    service = ParkingService(db)
    pass_ = service.buy_pass(garage.id, "ABC123", 1, EPOCH, MONTHLY_CENTS)
    ticket = service.enter(garage.id, "ABC123", pass_.valid_to - timedelta(hours=1))

    paid = service.pay(ticket.id, pass_.valid_to, CARD)
    assert paid.fee == Decimal("4.00")


# --- API: the active-pass lookup ignores the garage_id in the path and ----
# --- instead resolves it from the first pass matching the plate in any ----
# --- garage, so a pass in one garage is reported as active in another. ----


def test_get_active_pass_is_scoped_to_the_requested_garage(
    client, garage: Garage, session_factory: sessionmaker[Session]
) -> None:
    session = session_factory()
    other_garage = Garage(name="Uptown", capacity=2)
    session.add(other_garage)
    session.commit()
    other_garage_id = other_garage.id
    session.close()

    starts_at = datetime.now(UTC) - timedelta(days=1)
    resp = client.post(
        f"/garages/{garage.id}/passes",
        headers=HEADERS,
        json={"plate": "ABC123", "months": 1, "starts_at": starts_at.isoformat()},
    )
    assert resp.status_code == 201

    other = client.get(f"/garages/{other_garage_id}/passes/ABC123", headers=HEADERS)
    assert other.status_code == 404


# --- Design: buy_pass reads its price from a module constant instead of ---
# --- taking monthly_cents from the caller. ---------------------------------


def test_buy_pass_takes_monthly_cents_from_the_caller() -> None:
    params = inspect.signature(ParkingService.buy_pass).parameters
    assert "monthly_cents" in params
    assert "_DEFAULT_MONTHLY_CENTS" not in inspect.getsource(ParkingService.buy_pass)


# --- Design: pay duplicates the minutes computation in both branches -----
# --- instead of computing the fee once and then applying the pass. --------


def test_pay_computes_the_billable_minutes_once() -> None:
    source = inspect.getsource(ParkingService.pay)
    assert source.count("billable_minutes(") == 1


# --- Refactor: buy_pass accepts a notify flag that nothing reads. ---------


def test_buy_pass_has_no_unused_notify_parameter() -> None:
    assert "notify" not in inspect.signature(ParkingService.buy_pass).parameters


# --- Test: the shipped tests never buy a pass starting exactly when the ---
# --- previous one ends, so the half-open boundary of the overlap check ----
# --- is never exercised. ----------------------------------------------------


def test_buying_a_pass_exactly_when_the_previous_one_ends_is_allowed(
    db: Session, garage: Garage
) -> None:
    service = ParkingService(db)
    first = service.buy_pass(garage.id, "ABC123", 1, EPOCH, MONTHLY_CENTS)
    second = service.buy_pass(garage.id, "ABC123", 1, first.valid_to, MONTHLY_CENTS)
    assert second.valid_from == first.valid_to
