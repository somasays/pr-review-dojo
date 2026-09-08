"""Claim submission, decisions, and payouts: business rules layered on the
repositories.

Each public method opens exactly one unit of work (see
sandbox/expenses/db.py) and either fully succeeds or leaves no trace.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session, sessionmaker

from sandbox.expenses.db import Claim, ClaimLine, PayoutBatch, ensure_aware_utc, unit_of_work
from sandbox.expenses.domain.policy import (
    Category,
    ClaimStatus,
    PolicyLimit,
    line_violations,
    month_total_ok,
    transition,
)
from sandbox.expenses.repo import BatchRepo, ClaimRepo, EmployeeRepo

_CENTS = Decimal("0.01")

DEFAULT_LIMITS: dict[Category, PolicyLimit] = {
    Category.MEALS: PolicyLimit(Category.MEALS, Decimal("75.00"), Decimal("600.00")),
    Category.TRAVEL: PolicyLimit(Category.TRAVEL, Decimal("1500.00"), Decimal("4000.00")),
    Category.LODGING: PolicyLimit(Category.LODGING, Decimal("400.00"), Decimal("3000.00")),
    Category.EQUIPMENT: PolicyLimit(Category.EQUIPMENT, Decimal("1000.00"), Decimal("2000.00")),
}


class NotFound(Exception):
    pass


class NotAllowed(Exception):
    pass


class PolicyViolation(Exception):
    pass


@dataclass(frozen=True, slots=True)
class LineInput:
    category: Category
    amount: Decimal
    incurred_on: date
    note: str | None = None


def _quantize(amount: Decimal) -> Decimal:
    return amount.quantize(_CENTS, rounding=ROUND_HALF_UP)


class ClaimService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        limits: Mapping[Category, PolicyLimit] = DEFAULT_LIMITS,
    ) -> None:
        self.session_factory = session_factory
        self.limits = limits

    def submit(
        self,
        employee_email: str,
        idempotency_key: str,
        currency: str,
        lines: Sequence[LineInput],
    ) -> Claim:
        """Validate every line and the monthly cap per category, then
        submit. Repeating the same idempotency_key for the same employee
        returns the original claim instead of creating a second one."""
        if not lines:
            raise PolicyViolation("a claim must have at least one line")

        with unit_of_work(self.session_factory) as session:
            employees = EmployeeRepo(session)
            claims = ClaimRepo(session)

            employee = employees.by_email(employee_email)
            if employee is None or not employee.active:
                raise NotFound(f"employee {employee_email!r} not found or inactive")

            existing = claims.by_idempotency_key(employee.id, idempotency_key)
            if existing is not None:
                return existing

            violations: list[str] = []
            for line in lines:
                if line.amount <= 0:
                    violations.append(f"{line.category.value} line amount must be positive")
                    continue
                violations.extend(line_violations(line.amount, line.category, self.limits))
            if violations:
                raise PolicyViolation("; ".join(violations))

            month_new_totals: dict[tuple[Category, int, int], Decimal] = {}
            for line in lines:
                key = (line.category, line.incurred_on.year, line.incurred_on.month)
                month_new_totals[key] = month_new_totals.get(key, Decimal("0.00")) + line.amount
            for (category, year, month), new_amount in month_new_totals.items():
                existing_total = claims.month_total_for(employee.id, category, year, month)
                if not month_total_ok(existing_total, new_amount, self.limits.get(category)):
                    raise PolicyViolation(
                        f"{category.value} claims for {year:04d}-{month:02d} "
                        "would exceed the monthly cap"
                    )

            claim = Claim(
                id=str(uuid.uuid4()),
                employee_id=employee.id,
                currency=currency,
                status=ClaimStatus.DRAFT.value,
                idempotency_key=idempotency_key,
                lines=[
                    ClaimLine(
                        id=str(uuid.uuid4()),
                        category=line.category.value,
                        amount=_quantize(line.amount),
                        incurred_on=line.incurred_on,
                        note=line.note,
                    )
                    for line in lines
                ],
            )
            claim.status = transition(ClaimStatus(claim.status), ClaimStatus.SUBMITTED).value
            claim.submitted_at = datetime.now(UTC)
            return claims.add(claim)

    def decide(
        self, approver_email: str, claim_id: str, approve: bool, reason: str | None
    ) -> Claim:
        """Approve or reject a submitted claim. An approver may not decide
        a claim they filed themselves; rejecting one requires a reason."""
        if not approve and not reason:
            raise PolicyViolation("a reason is required to reject a claim")

        with unit_of_work(self.session_factory) as session:
            claims = ClaimRepo(session)
            employees = EmployeeRepo(session)

            claim = claims.get(claim_id)
            if claim is None:
                raise NotFound(f"claim {claim_id} not found")
            if claim.status != ClaimStatus.SUBMITTED.value:
                raise NotAllowed(f"claim {claim_id} is not submitted")

            owner = employees.get(claim.employee_id)
            if owner is not None and owner.email == approver_email:
                raise NotAllowed("an approver may not decide their own claim")

            target = ClaimStatus.APPROVED if approve else ClaimStatus.REJECTED
            claim.status = transition(ClaimStatus(claim.status), target).value
            claim.decided_at = datetime.now(UTC)
            claim.decided_by = approver_email
            return claim


class PayoutService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create_batch(self, now: datetime) -> PayoutBatch:
        """Collect every approved, unpaid claim into one batch and mark
        each paid. The total sums line amounts regardless of currency;
        PayoutBatch has no currency column, so this sandbox assumes a
        single reimbursement currency."""
        ensure_aware_utc(now)
        with unit_of_work(self.session_factory) as session:
            claims = ClaimRepo(session).approved_unpaid()
            total = Decimal("0.00")
            for claim in claims:
                total += sum((line.amount for line in claim.lines), Decimal("0.00"))

            batch = PayoutBatch(
                id=str(uuid.uuid4()), created_at=now, total=_quantize(total), count=len(claims)
            )
            BatchRepo(session).add(batch)

            for claim in claims:
                claim.status = transition(ClaimStatus(claim.status), ClaimStatus.PAID).value
                claim.paid_in_batch_id = batch.id
            return batch
