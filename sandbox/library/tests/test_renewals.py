"""Tests for LendingService.renew_loan and the /loans/{id}/renew endpoint."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from sandbox.library.db import Item, Patron
from sandbox.library.domain.lending import LoanStatus
from sandbox.library.service import LendingService, NotAllowed
from sandbox.library.tests.conftest import PATRON_KEY


def test_renew_extends_due_date_from_current_due_date(
    db: Session, seeded: dict[str, Patron | Item]
) -> None:
    service = LendingService(db)
    alice, book = seeded["alice"], seeded["book"]
    loan = service.checkout(alice.email, book.id, date(2024, 1, 1))
    assert loan.due_on == date(2024, 1, 15)

    renewed = service.renew_loan(loan.id, alice.email, date(2024, 1, 10))

    assert renewed.due_on == date(2024, 1, 29)  # 14 more days from the old due date
    assert renewed.renewals == 1


def test_renew_freezes_fine_accrued_before_renewal(
    db: Session, seeded: dict[str, Patron | Item]
) -> None:
    service = LendingService(db)
    alice, book = seeded["alice"], seeded["book"]
    loan = service.checkout(alice.email, book.id, date(2024, 1, 1))  # due 2024-01-15

    renewed = service.renew_loan(loan.id, alice.email, date(2024, 1, 25))

    assert renewed.frozen_fine == Decimal("2.00")  # 10 days late, 2 grace, 8 * 0.25
    assert renewed.due_on == date(2024, 1, 29)


def test_renew_refused_for_blocked_patron(db: Session, seeded: dict[str, Patron | Item]) -> None:
    service = LendingService(db)
    alice, book = seeded["alice"], seeded["book"]
    loan = service.checkout(alice.email, book.id, date(2024, 1, 1))
    alice.blocked = True
    db.flush()

    with pytest.raises(NotAllowed):
        service.renew_loan(loan.id, alice.email, date(2024, 1, 10))


def test_renew_refused_for_lost_loan(db: Session, seeded: dict[str, Patron | Item]) -> None:
    service = LendingService(db)
    alice, book = seeded["alice"], seeded["book"]
    loan = service.checkout(alice.email, book.id, date(2024, 1, 1))
    loan.status = LoanStatus.LOST.value
    db.flush()

    with pytest.raises(NotAllowed):
        service.renew_loan(loan.id, alice.email, date(2024, 1, 10))


def test_renew_refused_when_another_patron_holds_the_item(
    db: Session, seeded: dict[str, Patron | Item]
) -> None:
    service = LendingService(db)
    alice, bob, book = seeded["alice"], seeded["bob"], seeded["book"]
    loan = service.checkout(alice.email, book.id, date(2024, 1, 1))
    service.place_hold(bob.email, book.id, datetime.now(UTC))

    with pytest.raises(NotAllowed):
        service.renew_loan(loan.id, alice.email, date(2024, 1, 10))


def test_renew_endpoint_returns_updated_loan(client: TestClient, seeded) -> None:  # noqa: ANN001
    book = seeded["other_book"]
    headers = {"X-Library-Key": PATRON_KEY}

    created = client.post("/loans", json={"item_id": book.id}, headers=headers)
    loan_id = created.json()["id"]

    renewed = client.post(f"/loans/{loan_id}/renew", json={}, headers=headers)

    assert renewed.status_code == 200
    body = renewed.json()
    assert body["renewals"] == 1
    assert body["due_on"] > created.json()["due_on"]
