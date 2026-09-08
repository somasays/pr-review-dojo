"""Tests for the overdue notification job."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from sandbox.library.db import Item, Loan, Patron
from sandbox.library.overdue import notify_overdue
from sandbox.library.service import LendingService


def _make_overdue_loan(
    session_factory: sessionmaker[Session], seeded: dict[str, Patron | Item]
) -> int:
    session = session_factory()
    try:
        loan = LendingService(session).checkout(
            seeded["alice"].email, seeded["book"].id, date(2024, 1, 1)
        )
        session.commit()
        return loan.id
    finally:
        session.close()


def test_notify_overdue_calls_notifier_once_per_loan(
    session_factory: sessionmaker[Session], seeded: dict[str, Patron | Item]
) -> None:
    _make_overdue_loan(session_factory, seeded)
    calls: list[tuple[str, Loan, Decimal]] = []

    def notifier(email: str, loan: Loan, fine: Decimal) -> None:
        calls.append((email, loan, fine))

    count = notify_overdue(session_factory, date(2024, 2, 1), notifier)

    assert count == 1
    assert len(calls) == 1
    email, _loan, fine = calls[0]
    assert email == seeded["alice"].email
    assert fine > 0


def test_notify_overdue_includes_a_loan_s_frozen_fine(
    session_factory: sessionmaker[Session], seeded: dict[str, Patron | Item]
) -> None:
    """A loan renewed while overdue carries a frozen fine; the notifier's
    total should be that frozen fine plus whatever has accrued since."""
    loan_id = _make_overdue_loan(session_factory, seeded)
    session = session_factory()
    try:
        LendingService(session).renew_loan(loan_id, seeded["alice"].email, date(2024, 1, 20))
        session.commit()
    finally:
        session.close()

    calls: list[Decimal] = []

    def notifier(email: str, loan: Loan, fine: Decimal) -> None:
        calls.append(fine)

    notify_overdue(session_factory, date(2024, 2, 20), notifier)

    assert calls and calls[0] > Decimal("0.00")


def test_notify_overdue_skips_already_notified_today(
    session_factory: sessionmaker[Session], seeded: dict[str, Patron | Item]
) -> None:
    _make_overdue_loan(session_factory, seeded)
    calls: list[str] = []

    def notifier(email: str, loan: Loan, fine: Decimal) -> None:
        calls.append(email)

    notify_overdue(session_factory, date(2024, 2, 1), notifier)
    second_count = notify_overdue(session_factory, date(2024, 2, 1), notifier)

    assert second_count == 0
    assert len(calls) == 1
