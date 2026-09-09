"""Tests for the monthly pass feature."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from sandbox.parking.db import Garage
from sandbox.parking.service import OverlappingPass, ParkingService
from sandbox.parking.tests.conftest import CARD, EPOCH

MONTHLY_CENTS = 5000


def test_pass_covers_payment_and_makes_it_free(db: Session, garage: Garage) -> None:
    service = ParkingService(db)
    service.buy_pass(garage.id, "ABC123", 1, EPOCH, MONTHLY_CENTS)
    ticket = service.enter(garage.id, "ABC123", EPOCH + timedelta(days=5))
    paid = service.pay(ticket.id, EPOCH + timedelta(days=15), CARD)
    assert paid.fee == Decimal("0.00")


def test_second_pass_after_the_first_ends_is_allowed(db: Session, garage: Garage) -> None:
    service = ParkingService(db)
    first = service.buy_pass(garage.id, "ABC123", 1, EPOCH, MONTHLY_CENTS)
    second = service.buy_pass(
        garage.id, "ABC123", 1, first.valid_to + timedelta(days=7), MONTHLY_CENTS
    )
    assert second.valid_from > first.valid_to


def test_overlapping_pass_purchase_is_rejected(db: Session, garage: Garage) -> None:
    service = ParkingService(db)
    service.buy_pass(garage.id, "ABC123", 1, EPOCH, MONTHLY_CENTS)
    with pytest.raises(OverlappingPass):
        service.buy_pass(garage.id, "ABC123", 1, EPOCH + timedelta(days=10), MONTHLY_CENTS)
