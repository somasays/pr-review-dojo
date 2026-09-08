"""Tests for reporting a loan lost and reversing that report."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from sandbox.library.db import Item, Patron
from sandbox.library.domain.lending import LoanStatus
from sandbox.library.service import LendingService, NotAllowed
from sandbox.library.tests.conftest import LIBRARIAN_KEY, OTHER_PATRON_KEY, PATRON_KEY


def test_report_lost_charges_replacement_cost_plus_fine_and_drops_copies(
    db: Session, seeded: dict[str, Patron | Item]
) -> None:
    service = LendingService(db)
    alice, book = seeded["alice"], seeded["book"]
    loan = service.checkout(alice.email, book.id, date(2024, 1, 1))
    db.commit()

    result = service.report_lost(loan.id, "patron", alice.email, date(2024, 1, 5))
    assert result.loan.status == LoanStatus.LOST.value
    assert result.fee == Decimal("20.00")  # replacement cost only, no fine yet
    assert service.items.get(book.id).copies == 0


def test_report_lost_wrong_patron_raises(db: Session, seeded: dict[str, Patron | Item]) -> None:
    service = LendingService(db)
    alice, bob, book = seeded["alice"], seeded["bob"], seeded["book"]
    loan = service.checkout(alice.email, book.id, date(2024, 1, 1))
    db.commit()

    with pytest.raises(NotAllowed):
        service.report_lost(loan.id, "patron", bob.email, date(2024, 1, 2))


def test_reverse_loss_restores_copy_and_waives_fee_not_fine(
    db: Session, seeded: dict[str, Patron | Item]
) -> None:
    service = LendingService(db)
    alice, book = seeded["alice"], seeded["book"]
    loan = service.checkout(alice.email, book.id, date(2024, 1, 1))  # due 2024-01-15
    db.commit()
    lost = service.report_lost(loan.id, "patron", alice.email, date(2024, 1, 20))
    db.commit()
    assert lost.fee > Decimal("20.00")  # replacement cost plus an accrued fine

    reversed_result = service.reverse_loss(loan.id, date(2024, 1, 22))
    assert reversed_result.loan.status == LoanStatus.RETURNED.value
    assert reversed_result.fine > Decimal("0.00")  # the fine is not waived
    assert service.items.get(book.id).copies == 1


def test_report_lost_endpoint_self_ok_others_forbidden(
    client: TestClient,
    seeded,  # noqa: ANN001
) -> None:
    book = seeded["book"]
    patron_headers = {"X-Library-Key": PATRON_KEY}
    loan = client.post("/loans", json={"item_id": book.id}, headers=patron_headers).json()

    forbidden = client.post(
        f"/loans/{loan['id']}/report-lost", headers={"X-Library-Key": OTHER_PATRON_KEY}
    )
    assert forbidden.status_code == 403

    self_report = client.post(f"/loans/{loan['id']}/report-lost", headers=patron_headers)
    assert self_report.status_code == 200
    assert self_report.json()["loan"]["status"] == "lost"
    assert "fee" in self_report.json()


def test_report_lost_endpoint_librarian_on_behalf_then_reverses(
    client: TestClient,
    seeded,  # noqa: ANN001
) -> None:
    book = seeded["other_book"]
    patron_headers = {"X-Library-Key": PATRON_KEY}
    librarian_headers = {"X-Library-Key": LIBRARIAN_KEY}
    loan = client.post("/loans", json={"item_id": book.id}, headers=patron_headers).json()

    lost = client.post(f"/loans/{loan['id']}/report-lost", headers=librarian_headers)
    assert lost.status_code == 200

    reversed_response = client.post(f"/loans/{loan['id']}/reverse-loss", headers=librarian_headers)
    assert reversed_response.status_code == 200
    assert reversed_response.json()["loan"]["status"] == "returned"

    item = client.get(f"/items/{book.id}", headers=patron_headers)
    assert item.json()["available_copies"] == book.copies
