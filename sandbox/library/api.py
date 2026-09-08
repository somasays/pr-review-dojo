"""FastAPI app for the library lending service.

Auth is X-Library-Key. LIBRARY_KEYS is a comma-separated list of
"role:email:key" triples; a valid key resolves to the (role, email) it
authenticates. There are two roles: librarian and patron. Pydantic request
and response models are explicit allowlists, never ORM rows. The transaction
for every request is owned by `get_db`, via db.session_scope(); it commits
when the endpoint returns and rolls back if anything raises.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from os import environ
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from sandbox.library.db import Hold, Loan, session_scope
from sandbox.library.repo import ItemRepo, LoanRepo, PatronRepo
from sandbox.library.service import LendingService, NoCopies, NotAllowed, NotFound


def _keys() -> dict[str, tuple[str, str]]:
    """Parse LIBRARY_KEYS into {key: (role, email)}."""
    raw = environ.get("LIBRARY_KEYS", "")
    keys: dict[str, tuple[str, str]] = {}
    for triple in raw.split(","):
        role, _, rest = triple.strip().partition(":")
        email, _, key = rest.partition(":")
        if role and email and key:
            keys[key] = (role, email)
    return keys


def get_db() -> Iterator[Session]:
    with session_scope() as session:
        yield session


DbSession = Annotated[Session, Depends(get_db)]


def get_identity(x_library_key: Annotated[str | None, Header()] = None) -> tuple[str, str]:
    """The (role, email) for a valid X-Library-Key, of either role."""
    if not x_library_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-Library-Key")
    identity = _keys().get(x_library_key)
    if identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid X-Library-Key")
    return identity


Identity = Annotated[tuple[str, str], Depends(get_identity)]


def require_patron(identity: Identity) -> str:
    role, email = identity
    if role != "patron":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "a patron key is required")
    return email


PatronEmail = Annotated[str, Depends(require_patron)]


def require_librarian(identity: Identity) -> str:
    role, email = identity
    if role != "librarian":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "a librarian key is required")
    return email


Librarian = Annotated[str, Depends(require_librarian)]


def get_service(db: DbSession) -> LendingService:
    return LendingService(db)


Service = Annotated[LendingService, Depends(get_service)]


class LoanCreate(BaseModel):
    item_id: int


class LoanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: int
    patron_id: int
    checked_out_on: date
    due_on: date
    returned_on: date | None
    status: str
    renewals: int


class ReturnOut(BaseModel):
    loan: LoanOut
    fine: Decimal


class LostReportOut(BaseModel):
    loan: LoanOut
    fee: Decimal


class ReverseLossOut(BaseModel):
    loan: LoanOut
    fine: Decimal


class HoldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: int
    patron_id: int
    placed_at: datetime
    fulfilled_at: datetime | None


class ItemOut(BaseModel):
    id: int
    title: str
    copies: int
    available_copies: int


def _today() -> date:
    return datetime.now(UTC).date()


app = FastAPI(title="Library", version="0.1.0")


@app.post("/loans", response_model=LoanOut, status_code=status.HTTP_201_CREATED)
def create_loan(body: LoanCreate, patron_email: PatronEmail, service: Service) -> Loan:
    try:
        return service.checkout(patron_email, body.item_id, _today())
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except NotAllowed as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except NoCopies as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@app.post("/loans/{loan_id}/return", response_model=ReturnOut)
def return_loan(loan_id: int, patron_email: PatronEmail, service: Service) -> ReturnOut:
    try:
        result = service.return_item(loan_id, patron_email, _today())
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except NotAllowed as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return ReturnOut(loan=LoanOut.model_validate(result.loan), fine=result.fine)


@app.post("/items/{item_id}/holds", response_model=HoldOut, status_code=status.HTTP_201_CREATED)
def create_hold(item_id: int, patron_email: PatronEmail, service: Service) -> Hold:
    try:
        return service.place_hold(patron_email, item_id, datetime.now(UTC))
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except NotAllowed as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@app.post("/loans/{loan_id}/report-lost", response_model=LostReportOut)
def report_lost(loan_id: int, identity: Identity, service: Service) -> LostReportOut:
    """A patron may report their own loan lost; a librarian may report any
    patron's loan lost."""
    role, email = identity
    try:
        result = service.report_lost(loan_id, role, email, _today())
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except NotAllowed as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return LostReportOut(loan=LoanOut.model_validate(result.loan), fee=result.fee)


@app.post("/loans/{loan_id}/reverse-loss", response_model=ReverseLossOut)
def reverse_loss(loan_id: int, _librarian: Librarian, service: Service) -> ReverseLossOut:
    """Only a librarian may reverse a lost report, once the item turns up
    within the reversal window."""
    try:
        result = service.reverse_loss(loan_id, _today())
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except NotAllowed as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return ReverseLossOut(loan=LoanOut.model_validate(result.loan), fine=result.fine)


@app.get("/patrons/{patron_id}/loans", response_model=list[LoanOut])
def list_patron_loans(patron_id: int, identity: Identity, db: DbSession) -> Sequence[Loan]:
    """A patron's active loans. A librarian may view any patron; a patron
    may only view their own."""
    role, email = identity
    patron = PatronRepo(db).get(patron_id)
    if patron is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "patron not found")
    if role != "librarian" and patron.email != email:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not allowed to view this patron's loans")
    return LoanRepo(db).active_for_patron(patron_id)


@app.get("/items/{item_id}", response_model=ItemOut)
def get_item(item_id: int, _identity: Identity, db: DbSession) -> ItemOut:
    """Any valid key, librarian or patron, may look up an item."""
    items = ItemRepo(db)
    item = items.get(item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "item not found")
    return ItemOut(
        id=item.id,
        title=item.title,
        copies=item.copies,
        available_copies=items.available_copies(item_id),
    )
