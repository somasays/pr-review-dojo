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
    LineOutcome,
    LineRejection,
    PolicyLimit,
    claim_outcome,
    line_violations,
    month_total_ok,
    payable_total,
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
        self,
        approver_email: str,
        claim_id: str,
        approve: bool,
        reason: str | None,
        rejected_lines: Sequence[LineRejection] | None = None,
    ) -> Claim:
        """Approve or reject a submitted claim, in whole or line by line.

        Approving with `rejected_lines` marks those lines rejected, each
        with its own reason, and every other line approved; the claim's
        payable total is the sum of the approved lines, and the claim
        itself lands on rejected only when every line ends up rejected. An
        approver may not decide a claim they filed themselves. Repeating
        the same decision from the same approver returns the claim
        unchanged."""
        if not approve and not reason:
            raise PolicyViolation("a reason is required to reject a claim")

        with unit_of_work(self.session_factory) as session:
            claims = ClaimRepo(session)
            employees = EmployeeRepo(session)

            claim = claims.get(claim_id)
            if claim is None:
                raise NotFound(f"claim {claim_id} not found")

            if claim.status != ClaimStatus.SUBMITTED.value:
                if claim.decided_by == approver_email:
                    return claim
                raise NotAllowed(f"claim {claim_id} is not submitted")

            owner = employees.get(claim.employee_id)
            if owner is not None and owner.email == approver_email:
                raise NotAllowed("an approver may not decide their own claim")

            if not rejected_lines:
                outcome = LineOutcome.APPROVED if approve else LineOutcome.REJECTED
                for line in claim.lines:
                    line.outcome = outcome.value
                    if outcome is LineOutcome.REJECTED:
                        line.rejection_reason = reason
                approved_lines = [
                    line for line in claim.lines if line.outcome == LineOutcome.APPROVED.value
                ]
                claim.payable_total = sum((line.amount for line in approved_lines), Decimal("0.00"))
            else:
                rejected_by_id = {r.line_id: r.reason for r in rejected_lines}
                known_ids = {line.id for line in claim.lines}
                unknown_ids = set(rejected_by_id) - known_ids
                if unknown_ids:
                    raise NotFound(
                        f"line(s) {', '.join(sorted(unknown_ids))} not found on claim {claim_id}"
                    )
                for line in claim.lines:
                    if line.id in rejected_by_id:
                        line.outcome = LineOutcome.REJECTED.value
                        line.rejection_reason = rejected_by_id[line.id]
                    else:
                        line.outcome = LineOutcome.APPROVED.value
                approved_lines = [
                    line for line in claim.lines if line.outcome == LineOutcome.APPROVED.value
                ]
                claim.payable_total = payable_total([line.amount for line in approved_lines])

            approved_amounts = [line.amount for line in approved_lines]

            if approved_amounts:
                month_new_totals: dict[tuple[Category, int, int], Decimal] = {}
                for line in approved_lines:
                    key = (Category(line.category), line.incurred_on.year, line.incurred_on.month)
                    month_new_totals[key] = month_new_totals.get(key, Decimal("0.00")) + line.amount
                for (category, year, month), new_amount in month_new_totals.items():
                    existing_total = claims.approved_month_total_for(
                        claim.employee_id, category, year, month, exclude_claim_id=claim.id
                    )
                    if not month_total_ok(existing_total, new_amount, self.limits.get(category)):
                        raise PolicyViolation(
                            f"{category.value} claims for {year:04d}-{month:02d} "
                            "would exceed the monthly cap"
                        )

            target = claim_outcome(len(approved_lines), len(claim.lines))
            claim.status = transition(ClaimStatus(claim.status), target).value
            claim.decided_at = datetime.now(UTC)
            claim.decided_by = approver_email
            return claims.save(claim)


class PayoutService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create_batch(self, now: datetime) -> PayoutBatch:
        """Collect every approved, unpaid claim into one batch and mark
        each paid. Each claim pays its payable total, the sum of its
        approved lines, not the sum of every line it was submitted with.
        The total sums regardless of currency; PayoutBatch has no currency
        column, so this sandbox assumes a single reimbursement currency."""
        ensure_aware_utc(now)
        with unit_of_work(self.session_factory) as session:
            claims = ClaimRepo(session).approved_unpaid()
            total = Decimal("0.00")
            for claim in claims:
                total += claim.payable_total if claim.payable_total is not None else Decimal("0.00")

            batch = PayoutBatch(
                id=str(uuid.uuid4()), created_at=now, total=_quantize(total), count=len(claims)
            )
            BatchRepo(session).add(batch)

            for claim in claims:
                claim.status = transition(ClaimStatus(claim.status), ClaimStatus.PAID).value
                claim.paid_in_batch_id = batch.id
            return batch
