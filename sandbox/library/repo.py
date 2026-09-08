"""Repositories: the only place that builds queries.

Convention (see sandbox/library/README.md): repositories never commit. The
API's `get_db` dependency and the overdue job own the transaction through
db.session_scope(). Every query uses bound parameters; no f-string or
%-formatted SQL anywhere.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sandbox.library.db import Hold, Item, Loan, Patron
from sandbox.library.domain.lending import LoanStatus


class PatronRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, patron_id: int) -> Patron | None:
        return self.session.get(Patron, patron_id)

    def by_email(self, email: str) -> Patron | None:
        stmt = select(Patron).where(Patron.email == email)
        return self.session.scalars(stmt).first()


class ItemRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, item_id: int) -> Item | None:
        return self.session.get(Item, item_id)

    def available_copies(self, item_id: int) -> int:
        """The item's copies minus its currently active loans."""
        item = self.get(item_id)
        if item is None:
            return 0
        stmt = (
            select(func.count())
            .select_from(Loan)
            .where(Loan.item_id == item_id, Loan.status == LoanStatus.ACTIVE.value)
        )
        active_loans = self.session.scalar(stmt) or 0
        return item.copies - active_loans


class LoanRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, loan: Loan) -> Loan:
        self.session.add(loan)
        self.session.flush()
        return loan

    def get(self, loan_id: int) -> Loan | None:
        return self.session.get(Loan, loan_id)

    def active_for_patron(self, patron_id: int) -> Sequence[Loan]:
        stmt = (
            select(Loan)
            .where(Loan.patron_id == patron_id, Loan.status == LoanStatus.ACTIVE.value)
            .order_by(Loan.due_on)
        )
        return self.session.scalars(stmt).all()

    def overdue(self, as_of: date) -> Sequence[Loan]:
        """Active loans whose due date is strictly before `as_of`."""
        stmt = (
            select(Loan)
            .where(Loan.status == LoanStatus.ACTIVE.value, Loan.due_on < as_of)
            .order_by(Loan.due_on)
        )
        return self.session.scalars(stmt).all()


class HoldRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, hold: Hold) -> Hold:
        self.session.add(hold)
        self.session.flush()
        return hold

    def active_for_item(self, item_id: int) -> Sequence[Hold]:
        """Unfulfilled holds on this item, oldest first."""
        stmt = (
            select(Hold)
            .where(Hold.item_id == item_id, Hold.fulfilled_at.is_(None))
            .order_by(Hold.placed_at)
        )
        return self.session.scalars(stmt).all()

    def first_for_item(self, item_id: int) -> Hold | None:
        """The oldest unfulfilled hold on this item, if any."""
        stmt = (
            select(Hold)
            .where(Hold.item_id == item_id, Hold.fulfilled_at.is_(None))
            .order_by(Hold.placed_at)
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def fulfill(self, hold_id: int, when: datetime) -> None:
        hold = self.session.get(Hold, hold_id)
        if hold is not None:
            hold.fulfilled_at = when
