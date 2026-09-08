"""Lending service: business rules layered on the repositories.

This service does not commit. It runs inside a session opened by the
caller (the API's `get_db` dependency, or a test fixture) and that caller
owns the transaction boundary (see sandbox/library/README.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from sandbox.library.db import Hold, Loan, ensure_aware_utc
from sandbox.library.domain.lending import LoanStatus, can_renew, due_date, fine_for, transition
from sandbox.library.repo import HoldRepo, ItemRepo, LoanRepo, PatronRepo

LOAN_DAYS = 14
WEEKENDS_EXCLUDED = False
GRACE_DAYS = 2
FINE_PER_DAY = Decimal("0.25")
FINE_CAP = Decimal("10.00")
MAX_RENEWALS = 2


class NotFound(Exception):
    pass


class NotAllowed(Exception):
    pass


class NoCopies(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ReturnResult:
    loan: Loan
    fine: Decimal


class LendingService:
    def __init__(self, session: Session) -> None:
        self.patrons = PatronRepo(session)
        self.items = ItemRepo(session)
        self.loans = LoanRepo(session)
        self.holds = HoldRepo(session)

    def checkout(self, patron_email: str, item_id: int, today: date) -> Loan:
        """Check out an item to a patron.

        Blocked patrons are refused outright. If the item has any active
        holds, only the oldest hold's patron may check it out, whatever the
        copy count; otherwise the usual copy count applies.
        """
        patron = self.patrons.by_email(patron_email)
        if patron is None:
            raise NotFound(f"patron {patron_email!r} not found")
        if patron.blocked:
            raise NotAllowed(f"patron {patron_email!r} is blocked")
        item = self.items.get(item_id)
        if item is None:
            raise NotFound(f"item {item_id} not found")

        first_hold = self.holds.first_for_item(item_id)
        if first_hold is not None and first_hold.patron_id != patron.id:
            raise NotAllowed(f"item {item_id} is held for another patron")
        if self.items.available_copies(item_id) <= 0:
            raise NoCopies(f"no copies of item {item_id} are available")

        loan = self.loans.add(
            Loan(
                item_id=item_id,
                patron_id=patron.id,
                checked_out_on=today,
                due_on=due_date(today, LOAN_DAYS, WEEKENDS_EXCLUDED),
                status=LoanStatus.ACTIVE.value,
                renewals=0,
            )
        )
        if first_hold is not None and first_hold.patron_id == patron.id:
            self.holds.fulfill(first_hold.id, datetime.now(UTC))
        return loan

    def return_item(self, loan_id: int, patron_email: str, today: date) -> ReturnResult:
        """Return a loan and compute the fine owed, if any."""
        patron = self.patrons.by_email(patron_email)
        if patron is None:
            raise NotFound(f"patron {patron_email!r} not found")
        loan = self.loans.get(loan_id)
        if loan is None:
            raise NotFound(f"loan {loan_id} not found")
        if loan.patron_id != patron.id:
            raise NotAllowed(f"loan {loan_id} does not belong to {patron_email!r}")
        if loan.status != LoanStatus.ACTIVE.value:
            raise NotAllowed(f"loan {loan_id} is not active")

        fine = fine_for(loan.due_on, today, GRACE_DAYS, FINE_PER_DAY, FINE_CAP)
        loan.status = transition(LoanStatus(loan.status), LoanStatus.RETURNED).value
        loan.returned_on = today
        return ReturnResult(loan=loan, fine=fine)

    def renew_loan(self, loan_id: int, patron_email: str, today: date) -> Loan:
        """Renew an active loan, extending its due date from its current due date.

        Any fine already accrued is frozen onto the loan before the due date
        moves, so extending the loan never erases what is already owed.
        """
        patron = self.patrons.by_email(patron_email)
        if patron is None:
            raise NotFound(f"patron {patron_email!r} not found")
        if patron.blocked:
            raise NotAllowed(f"patron {patron_email!r} is blocked")
        loan = self.loans.get(loan_id)
        if loan is None:
            raise NotFound(f"loan {loan_id} not found")
        if loan.patron_id != patron.id:
            raise NotAllowed(f"loan {loan_id} does not belong to {patron_email!r}")
        if loan.status == LoanStatus.LOST.value:
            raise NotAllowed(f"loan {loan_id} is lost and cannot be renewed")
        if loan.status == LoanStatus.RETURNED.value:
            raise NotAllowed(f"loan {loan_id} has already been returned")
        if self.holds.other_patron_holds(loan.item_id, patron.id):
            raise NotAllowed(f"item {loan.item_id} is held for another patron")
        if not can_renew(loan.renewals, MAX_RENEWALS, has_hold=False):
            raise NotAllowed(f"loan {loan_id} has reached the maximum number of renewals")

        fine_so_far = fine_for(loan.due_on, today, GRACE_DAYS, FINE_PER_DAY, FINE_CAP)
        loan.frozen_fine = loan.frozen_fine + fine_so_far
        loan.due_on = due_date(loan.due_on, LOAN_DAYS, WEEKENDS_EXCLUDED)
        loan.renewals += 1
        return loan

    def place_hold(self, patron_email: str, item_id: int, now: datetime) -> Hold:
        """Place a hold for a patron on an item."""
        ensure_aware_utc(now)
        patron = self.patrons.by_email(patron_email)
        if patron is None:
            raise NotFound(f"patron {patron_email!r} not found")
        if patron.blocked:
            raise NotAllowed(f"patron {patron_email!r} is blocked")
        item = self.items.get(item_id)
        if item is None:
            raise NotFound(f"item {item_id} not found")
        return self.holds.add(Hold(item_id=item_id, patron_id=patron.id, placed_at=now))
