"""Tests for LendingService and the transaction boundary it runs inside."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from sandbox.library.db import Item, Patron, session_scope
from sandbox.library.domain.lending import LoanStatus
from sandbox.library.repo import HoldRepo, LoanRepo
from sandbox.library.service import LendingService, NoCopies, NotAllowed


def test_checkout_success(db: Session, seeded: dict[str, Patron | Item]) -> None:
    service = LendingService(db)
    alice = seeded["alice"]
    book = seeded["book"]
    loan = service.checkout(alice.email, book.id, date(2024, 1, 1))
    assert loan.status == LoanStatus.ACTIVE.value
    assert loan.due_on == date(2024, 1, 15)  # default 14-day loan
    assert service.items.available_copies(book.id) == 0


def test_checkout_blocked_patron_raises(db: Session, seeded: dict[str, Patron | Item]) -> None:
    alice = seeded["alice"]
    alice.blocked = True
    db.flush()
    service = LendingService(db)
    with pytest.raises(NotAllowed):
        service.checkout(alice.email, seeded["book"].id, date(2024, 1, 1))


def test_checkout_no_copies_raises(db: Session, seeded: dict[str, Patron | Item]) -> None:
    service = LendingService(db)
    alice, bob, book = seeded["alice"], seeded["bob"], seeded["book"]
    service.checkout(alice.email, book.id, date(2024, 1, 1))  # takes the one copy
    with pytest.raises(NoCopies):
        service.checkout(bob.email, book.id, date(2024, 1, 1))


def test_checkout_respects_hold_queue(db: Session, seeded: dict[str, Patron | Item]) -> None:
    """A hold reserves the item for its holder, and checking it out fulfills
    the hold so it does not go on blocking the item forever."""
    service = LendingService(db)
    alice, bob, book = seeded["alice"], seeded["bob"], seeded["book"]
    service.place_hold(bob.email, book.id, datetime.now(UTC))

    with pytest.raises(NotAllowed):
        service.checkout(alice.email, book.id, date(2024, 1, 1))

    service.checkout(bob.email, book.id, date(2024, 1, 1))
    assert HoldRepo(db).first_for_item(book.id) is None


def test_return_item_computes_fine_and_updates_status(
    db: Session, seeded: dict[str, Patron | Item]
) -> None:
    service = LendingService(db)
    alice, book = seeded["alice"], seeded["book"]
    loan = service.checkout(alice.email, book.id, date(2024, 1, 1))
    result = service.return_item(loan.id, alice.email, date(2024, 1, 20))
    assert result.fine > 0
    assert result.loan.status == LoanStatus.RETURNED.value
    assert result.loan.returned_on == date(2024, 1, 20)
    assert service.items.available_copies(book.id) == 1


def test_return_item_wrong_patron_raises(db: Session, seeded: dict[str, Patron | Item]) -> None:
    service = LendingService(db)
    alice, bob, book = seeded["alice"], seeded["bob"], seeded["book"]
    loan = service.checkout(alice.email, book.id, date(2024, 1, 1))
    with pytest.raises(NotAllowed):
        service.return_item(loan.id, bob.email, date(2024, 1, 5))


def test_session_scope_rolls_back_on_error(
    session_factory: sessionmaker[Session], seeded: dict[str, Patron | Item]
) -> None:
    """A failure partway through a unit of work leaves no partial write."""
    alice, book = seeded["alice"], seeded["book"]

    with pytest.raises(RuntimeError), session_scope(session_factory) as session:
        LendingService(session).checkout(alice.email, book.id, date(2024, 1, 1))
        raise RuntimeError("boom")

    with session_factory() as check:
        assert LoanRepo(check).active_for_patron(alice.id) == []
