"""Tests for sandbox/expenses/service.py."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session, sessionmaker

from sandbox.expenses.domain.policy import Category, PolicyLimit
from sandbox.expenses.service import (
    ClaimService,
    LineInput,
    NotAllowed,
    NotFound,
    PayoutService,
    PolicyViolation,
)
from sandbox.expenses.tests.conftest import EMPLOYEE_EMAIL, SELF_APPROVER_EMAIL

INCURRED = date(2026, 6, 15)


@pytest.fixture
def claim_service(session_factory: sessionmaker[Session]) -> ClaimService:
    return ClaimService(session_factory)


@pytest.fixture
def payout_service(session_factory: sessionmaker[Session]) -> PayoutService:
    return PayoutService(session_factory)


def _line(amount: str, category: Category = Category.MEALS) -> LineInput:
    return LineInput(category=category, amount=Decimal(amount), incurred_on=INCURRED)


def test_submit_creates_submitted_claim(claim_service: ClaimService, seeded) -> None:
    claim = claim_service.submit(EMPLOYEE_EMAIL, "key-1", "USD", [_line("20.00")])
    assert claim.status == "submitted"
    assert claim.submitted_at is not None
    assert len(claim.lines) == 1


def test_submit_is_idempotent(claim_service: ClaimService, seeded) -> None:
    first = claim_service.submit(EMPLOYEE_EMAIL, "key-2", "USD", [_line("20.00")])
    second = claim_service.submit(EMPLOYEE_EMAIL, "key-2", "USD", [_line("20.00")])
    assert first.id == second.id


def test_submit_rejects_line_over_policy_max(claim_service: ClaimService, seeded) -> None:
    with pytest.raises(PolicyViolation):
        claim_service.submit(EMPLOYEE_EMAIL, "key-3", "USD", [_line("10000.00")])


def test_submit_rejects_month_cap_exceeded(session_factory: sessionmaker[Session], seeded) -> None:
    tight = {Category.MEALS: PolicyLimit(Category.MEALS, Decimal("100.00"), Decimal("100.00"))}
    service = ClaimService(session_factory, limits=tight)
    service.submit(EMPLOYEE_EMAIL, "key-4", "USD", [_line("60.00")])
    with pytest.raises(PolicyViolation):
        service.submit(EMPLOYEE_EMAIL, "key-5", "USD", [_line("60.00")])


def test_submit_raises_not_found_for_unknown_employee(claim_service: ClaimService, seeded) -> None:
    with pytest.raises(NotFound):
        claim_service.submit("nobody@example.com", "key-6", "USD", [_line("20.00")])


def test_decide_approves_claim(claim_service: ClaimService, seeded) -> None:
    claim = claim_service.submit(EMPLOYEE_EMAIL, "key-7", "USD", [_line("20.00")])
    decided = claim_service.decide(SELF_APPROVER_EMAIL, claim.id, True, None)
    assert decided.status == "approved"
    assert decided.decided_by == SELF_APPROVER_EMAIL
    assert decided.decided_at is not None


def test_decide_rejects_own_claim(claim_service: ClaimService, seeded) -> None:
    claim = claim_service.submit(SELF_APPROVER_EMAIL, "key-8", "USD", [_line("20.00")])
    with pytest.raises(NotAllowed):
        claim_service.decide(SELF_APPROVER_EMAIL, claim.id, True, None)


def test_decide_requires_reason_to_reject(claim_service: ClaimService, seeded) -> None:
    claim = claim_service.submit(EMPLOYEE_EMAIL, "key-9", "USD", [_line("20.00")])
    with pytest.raises(PolicyViolation):
        claim_service.decide(SELF_APPROVER_EMAIL, claim.id, False, None)
    decided = claim_service.decide(SELF_APPROVER_EMAIL, claim.id, False, "receipts missing")
    assert decided.status == "rejected"


def test_create_batch_marks_claims_paid_and_sets_total(
    claim_service: ClaimService, payout_service: PayoutService, seeded
) -> None:
    claim = claim_service.submit(EMPLOYEE_EMAIL, "key-10", "USD", [_line("20.00"), _line("5.00")])
    claim_service.decide(SELF_APPROVER_EMAIL, claim.id, True, None)
    batch = payout_service.create_batch(datetime.now(UTC))
    assert batch.count == 1
    assert batch.total == Decimal("25.00")


def test_create_batch_rejects_naive_datetime(payout_service: PayoutService, seeded) -> None:
    with pytest.raises(ValueError):
        payout_service.create_batch(datetime(2026, 6, 15))
