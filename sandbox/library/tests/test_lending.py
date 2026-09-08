"""Tests for the pure domain logic in sandbox/library/domain/lending.py."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from sandbox.library.domain.lending import (
    InvalidTransition,
    LoanStatus,
    can_renew,
    due_date,
    fine_for,
    transition,
)


def test_due_date_without_weekends() -> None:
    assert due_date(date(2024, 1, 1), 5, weekends_excluded=False) == date(2024, 1, 6)


def test_due_date_excluding_weekends() -> None:
    # 2024-01-05 is a Friday. One business day lands on Monday 2024-01-08,
    # skipping the intervening Saturday and Sunday.
    friday = date(2024, 1, 5)
    assert due_date(friday, 1, weekends_excluded=True) == date(2024, 1, 8)
    assert due_date(friday, 2, weekends_excluded=True) == date(2024, 1, 9)


def test_fine_for_grace_period_is_zero() -> None:
    due = date(2024, 1, 1)
    returned_within_grace = date(2024, 1, 3)  # 2 days late, grace is 2 days
    assert fine_for(due, returned_within_grace, 2, Decimal("0.25"), Decimal("10.00")) == Decimal(
        "0.00"
    )
    returned_on_time = date(2024, 1, 1)
    assert fine_for(due, returned_on_time, 2, Decimal("0.25"), Decimal("10.00")) == Decimal("0.00")


def test_fine_for_after_grace() -> None:
    due = date(2024, 1, 1)
    returned = date(2024, 1, 10)  # 9 days late, 2 grace days, 7 billable days
    fine = fine_for(due, returned, 2, Decimal("0.25"), Decimal("10.00"))
    assert fine == Decimal("1.75")


def test_fine_for_capped() -> None:
    due = date(2024, 1, 1)
    returned = date(2024, 2, 1)  # far past due, would exceed the cap uncapped
    fine = fine_for(due, returned, 0, Decimal("1.00"), Decimal("10.00"))
    assert fine == Decimal("10.00")


def test_transition_valid_and_invalid() -> None:
    assert transition(LoanStatus.ACTIVE, LoanStatus.RETURNED) is LoanStatus.RETURNED
    assert transition(LoanStatus.ACTIVE, LoanStatus.LOST) is LoanStatus.LOST
    with pytest.raises(InvalidTransition):
        transition(LoanStatus.RETURNED, LoanStatus.ACTIVE)
    with pytest.raises(InvalidTransition):
        transition(LoanStatus.LOST, LoanStatus.RETURNED)


def test_can_renew() -> None:
    assert can_renew(renewals_so_far=0, max_renewals=2, has_hold=False) is True
    assert can_renew(renewals_so_far=2, max_renewals=2, has_hold=False) is False
    assert can_renew(renewals_so_far=0, max_renewals=2, has_hold=True) is False
