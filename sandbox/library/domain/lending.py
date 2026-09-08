"""Loan status, dates, and fine math: pure logic with no IO.

See sandbox/library/README.md for why this package's conventions (integer
ids, Decimal money, a single transition function for loan status) differ
from app/domain, sandbox/rooms/domain, and sandbox/lockers/domain.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

_CENTS = Decimal("0.01")


class LoanStatus(Enum):
    ACTIVE = "active"
    RETURNED = "returned"
    LOST = "lost"


class InvalidTransition(Exception):
    pass


# The only transitions a loan may ever make. Both returned and lost are
# terminal: once a loan leaves active, it never changes status again.
_ALLOWED_TRANSITIONS: dict[LoanStatus, frozenset[LoanStatus]] = {
    LoanStatus.ACTIVE: frozenset({LoanStatus.RETURNED, LoanStatus.LOST}),
    LoanStatus.RETURNED: frozenset(),
    LoanStatus.LOST: frozenset(),
}


def transition(current: LoanStatus, target: LoanStatus) -> LoanStatus:
    """The only way a loan's status is allowed to change.

    Returns `target` when the move is legal, raises InvalidTransition
    otherwise. Every place in this codebase that changes a loan's status
    goes through this function.
    """
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(f"cannot move a loan from {current.value} to {target.value}")
    return target


def due_date(checked_out_on: date, loan_days: int, weekends_excluded: bool) -> date:
    """The date a loan checked out on `checked_out_on` falls due.

    With `weekends_excluded` False, this is simply `loan_days` calendar days
    later. With it True, Saturdays and Sundays do not count, so a loan
    checked out on a Friday with a 1-day loan period is due the following
    Monday, not Saturday.
    """
    if loan_days < 0:
        raise ValueError("loan_days must not be negative")
    if not weekends_excluded:
        return checked_out_on + timedelta(days=loan_days)
    result = checked_out_on
    remaining = loan_days
    while remaining > 0:
        result += timedelta(days=1)
        if result.weekday() < 5:  # Monday=0 .. Sunday=6
            remaining -= 1
    return result


def fine_for(
    due: date, returned_on: date, grace_days: int, per_day: Decimal, cap: Decimal
) -> Decimal:
    """The fine owed for returning a loan on `returned_on`, quantized to cents.

    Nothing is owed for a loan returned on or before its due date, or during
    the grace period that follows it. Every day late beyond the grace period
    bills at `per_day`, and the total never exceeds `cap`.
    """
    if grace_days < 0:
        raise ValueError("grace_days must not be negative")
    if returned_on <= due:
        return Decimal("0.00")
    days_late = (returned_on - due).days
    billable_days = max(days_late - grace_days, 0)
    fine = (per_day * billable_days).quantize(_CENTS, rounding=ROUND_HALF_UP)
    return min(fine, cap.quantize(_CENTS, rounding=ROUND_HALF_UP))


def can_renew(renewals_so_far: int, max_renewals: int, has_hold: bool) -> bool:
    """Whether a loan may be renewed again.

    A loan may not be renewed past `max_renewals`, and never while another
    patron has an active hold on the item, however many renewals remain.
    """
    if has_hold:
        return False
    return renewals_so_far < max_renewals
