"""Repositories: the only place that builds queries.

Convention (see sandbox/expenses/README.md): repositories flush but never
commit. The service layer owns the transaction through db.unit_of_work().
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from sandbox.expenses.db import Claim, ClaimLine, Employee, PayoutBatch
from sandbox.expenses.domain.policy import Category, ClaimStatus

_CENTS = Decimal("0.01")


class EmployeeRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def by_email(self, email: str) -> Employee | None:
        stmt = select(Employee).where(Employee.email == email)
        return self.session.scalars(stmt).first()

    def get(self, employee_id: str) -> Employee | None:
        return self.session.get(Employee, employee_id)


class ClaimRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, claim: Claim) -> Claim:
        self.session.add(claim)
        self.session.flush()
        return claim

    def get(self, claim_id: str) -> Claim | None:
        stmt = select(Claim).where(Claim.id == claim_id).options(selectinload(Claim.lines))
        return self.session.scalars(stmt).first()

    def by_idempotency_key(self, employee_id: str, key: str) -> Claim | None:
        stmt = (
            select(Claim)
            .where(Claim.employee_id == employee_id, Claim.idempotency_key == key)
            .options(selectinload(Claim.lines))
        )
        return self.session.scalars(stmt).first()

    def month_total_for(
        self, employee_id: str, category: Category, year: int, month: int
    ) -> Decimal:
        """Sum of line amounts in this category for this employee's
        submitted or approved claims incurred in this year and month."""
        start = date(year, month, 1)
        end = date(year, month, monthrange(year, month)[1])
        stmt = (
            select(func.sum(ClaimLine.amount))
            .join(Claim, ClaimLine.claim_id == Claim.id)
            .where(
                Claim.employee_id == employee_id,
                Claim.status.in_([ClaimStatus.SUBMITTED.value, ClaimStatus.APPROVED.value]),
                ClaimLine.category == category.value,
                ClaimLine.incurred_on >= start,
                ClaimLine.incurred_on <= end,
            )
        )
        total = self.session.scalar(stmt)
        return Decimal(str(total)).quantize(_CENTS) if total is not None else Decimal("0.00")

    def submitted(self) -> Sequence[Claim]:
        """Claims awaiting a decision, oldest first."""
        stmt = (
            select(Claim)
            .where(Claim.status == ClaimStatus.SUBMITTED.value)
            .options(selectinload(Claim.lines))
            .order_by(Claim.submitted_at)
        )
        return self.session.scalars(stmt).all()

    def approved_unpaid(self) -> Sequence[Claim]:
        stmt = (
            select(Claim)
            .where(Claim.status == ClaimStatus.APPROVED.value, Claim.paid_in_batch_id.is_(None))
            .options(selectinload(Claim.lines))
        )
        return self.session.scalars(stmt).all()

    def save(self, claim: Claim) -> Claim:
        """Flush a decided claim's line outcomes and status. `add` does not
        apply here since the claim already exists."""
        self.session.flush()
        return claim


class BatchRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, batch: PayoutBatch) -> PayoutBatch:
        self.session.add(batch)
        self.session.flush()
        return batch
