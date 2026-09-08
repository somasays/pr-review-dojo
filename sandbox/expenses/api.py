"""FastAPI app for the expenses service.

Auth is X-Expenses-Key: EXPENSES_KEYS is a comma-separated list of
"role:email:key" triples, role being employee or approver. Pydantic
models are explicit allowlists, never ORM rows. Every write goes through
a service method, whose unit_of_work() owns the transaction, so these
handlers never commit.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from os import environ
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from sandbox.expenses.db import Claim, PayoutBatch, get_session_factory
from sandbox.expenses.domain.policy import Category
from sandbox.expenses.repo import ClaimRepo, EmployeeRepo
from sandbox.expenses.service import (
    ClaimService,
    LineInput,
    NotAllowed,
    NotFound,
    PayoutService,
    PolicyViolation,
)


def _keys() -> dict[str, tuple[str, str]]:
    """Parse EXPENSES_KEYS into {key: (role, email)}."""
    raw = environ.get("EXPENSES_KEYS", "")
    keys: dict[str, tuple[str, str]] = {}
    for triple in raw.split(","):
        role, _, rest = triple.strip().partition(":")
        email, _, key = rest.partition(":")
        if role and email and key:
            keys[key] = (role, email)
    return keys


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]


def get_session_factory_dep() -> sessionmaker[Session]:
    return get_session_factory()


SessionFactoryDep = Annotated[sessionmaker[Session], Depends(get_session_factory_dep)]


def get_claim_service(session_factory: SessionFactoryDep) -> ClaimService:
    return ClaimService(session_factory)


def get_payout_service(session_factory: SessionFactoryDep) -> PayoutService:
    return PayoutService(session_factory)


ClaimServiceDep = Annotated[ClaimService, Depends(get_claim_service)]
PayoutServiceDep = Annotated[PayoutService, Depends(get_payout_service)]


def get_identity(x_expenses_key: Annotated[str | None, Header()] = None) -> tuple[str, str]:
    """The (role, email) for a valid X-Expenses-Key, of either role."""
    if not x_expenses_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-Expenses-Key")
    identity = _keys().get(x_expenses_key)
    if identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid X-Expenses-Key")
    return identity


Identity = Annotated[tuple[str, str], Depends(get_identity)]


def require_employee(identity: Identity) -> str:
    role, email = identity
    if role != "employee":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "an employee key is required")
    return email


def require_approver(identity: Identity) -> str:
    role, email = identity
    if role != "approver":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "an approver key is required")
    return email


EmployeeEmail = Annotated[str, Depends(require_employee)]
ApproverEmail = Annotated[str, Depends(require_approver)]


class LineIn(BaseModel):
    category: Category
    amount: Decimal
    incurred_on: date
    note: str | None = None


class ClaimCreate(BaseModel):
    idempotency_key: str
    currency: str = Field(min_length=3, max_length=3)
    lines: list[LineIn]


class LineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    category: str
    amount: Decimal
    incurred_on: date
    note: str | None


class ClaimOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    employee_id: str
    currency: str
    status: str
    idempotency_key: str
    submitted_at: datetime | None
    decided_at: datetime | None
    decided_by: str | None
    paid_in_batch_id: str | None
    lines: list[LineOut]


class DecisionIn(BaseModel):
    approve: bool
    reason: str | None = None


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    total: Decimal
    count: int


app = FastAPI(title="Expenses", version="0.1.0")


@app.post("/claims", response_model=ClaimOut, status_code=status.HTTP_201_CREATED)
def create_claim(
    body: ClaimCreate, employee_email: EmployeeEmail, service: ClaimServiceDep
) -> Claim:
    lines = [
        LineInput(
            category=line.category,
            amount=line.amount,
            incurred_on=line.incurred_on,
            note=line.note,
        )
        for line in body.lines
    ]
    try:
        return service.submit(employee_email, body.idempotency_key, body.currency, lines)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PolicyViolation as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@app.get("/claims/pending", response_model=list[ClaimOut])
def list_pending_claims(_approver_email: ApproverEmail, db: DbSession) -> Sequence[Claim]:
    return ClaimRepo(db).submitted()


@app.get("/claims/{claim_id}", response_model=ClaimOut)
def get_claim(claim_id: str, identity: Identity, db: DbSession) -> Claim:
    """The claim's own employee, or any approver, may read it."""
    role, email = identity
    claim = ClaimRepo(db).get(claim_id)
    if claim is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "claim not found")
    if role != "approver":
        owner = EmployeeRepo(db).get(claim.employee_id)
        if owner is None or owner.email != email:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not allowed to view this claim")
    return claim


@app.post("/claims/{claim_id}/decision", response_model=ClaimOut)
def decide_claim(
    claim_id: str, body: DecisionIn, approver_email: ApproverEmail, service: ClaimServiceDep
) -> Claim:
    try:
        return service.decide(approver_email, claim_id, body.approve, body.reason)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except NotAllowed as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except PolicyViolation as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@app.post("/payouts/batches", response_model=BatchOut, status_code=status.HTTP_201_CREATED)
def create_payout_batch(_approver_email: ApproverEmail, service: PayoutServiceDep) -> PayoutBatch:
    return service.create_batch(datetime.now(UTC))
