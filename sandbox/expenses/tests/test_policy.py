"""Tests for sandbox/expenses/domain/policy.py."""

from __future__ import annotations

from decimal import Decimal

import pytest

from sandbox.expenses.domain.policy import (
    Category,
    ClaimStatus,
    InvalidTransition,
    PolicyLimit,
    convert,
    line_violations,
    month_total_ok,
    transition,
)

MEALS_LIMIT = PolicyLimit(Category.MEALS, Decimal("75.00"), Decimal("600.00"))
LIMITS = {Category.MEALS: MEALS_LIMIT}


def test_line_violations_ok_under_and_at_boundary() -> None:
    assert line_violations(Decimal("50.00"), Category.MEALS, LIMITS) == []
    assert line_violations(Decimal("75.00"), Category.MEALS, LIMITS) == []
    # a category with no configured limit never violates
    assert line_violations(Decimal("9999.00"), Category.TRAVEL, LIMITS) == []


def test_line_violations_over_limit() -> None:
    violations = line_violations(Decimal("75.01"), Category.MEALS, LIMITS)
    assert len(violations) == 1
    assert "meals" in violations[0]


def test_month_total_ok_boundary_and_over() -> None:
    assert month_total_ok(Decimal("500.00"), Decimal("100.00"), MEALS_LIMIT) is True  # == 600
    assert month_total_ok(Decimal("500.01"), Decimal("100.00"), MEALS_LIMIT) is False
    assert month_total_ok(Decimal("10000.00"), Decimal("1.00"), None) is True


def test_convert_rounds_half_up_and_rejects_negative_precision() -> None:
    # exactly on the boundary between 10.00 and 10.01; half up rounds up
    assert convert(Decimal("10.00"), Decimal("1.0005"), 2) == Decimal("10.01")
    assert convert(Decimal("100.00"), Decimal("0.85"), 2) == Decimal("85.00")
    with pytest.raises(ValueError):
        convert(Decimal("10.00"), Decimal("1.00"), -1)


def test_transition_allows_legal_chain() -> None:
    status = ClaimStatus.DRAFT
    status = transition(status, ClaimStatus.SUBMITTED)
    status = transition(status, ClaimStatus.APPROVED)
    status = transition(status, ClaimStatus.PAID)
    assert status is ClaimStatus.PAID


def test_transition_rejects_illegal_move() -> None:
    with pytest.raises(InvalidTransition):
        transition(ClaimStatus.DRAFT, ClaimStatus.APPROVED)
