"""FastAPI app for the parking service.

Auth is X-Attendant-Key: PARKING_KEYS is a comma-separated list of keys,
one role, an attendant, required on every endpoint. Pydantic models are
allowlists, never ORM rows. `get_db` owns the transaction through
`db.session_scope()`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from os import environ
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from sandbox.parking.db import Pass, Ticket, get_session_factory, session_scope
from sandbox.parking.domain.pricing import RateCard
from sandbox.parking.repo import GarageRepo, PassRepo, TicketRepo
from sandbox.parking.service import (
    AlreadyOpen,
    Full,
    NotFound,
    NotPaid,
    OverlappingPass,
    ParkingService,
)

# A single default rate card. This sandbox has no rate-management
# endpoint; every fee is computed with this one.
DEFAULT_CARD = RateCard(
    grace_minutes=15,
    first_hour_cents=400,
    extra_hour_cents=200,
    daily_cap_cents=2000,
    lost_ticket_cents=5000,
)


def _keys() -> set[str]:
    """Parse PARKING_KEYS into the set of valid attendant keys."""
    raw = environ.get("PARKING_KEYS", "")
    return {key.strip() for key in raw.split(",") if key.strip()}


def get_db() -> Iterator[Session]:
    with session_scope(get_session_factory()) as session:
        yield session


DbSession = Annotated[Session, Depends(get_db)]


def require_attendant(x_attendant_key: Annotated[str | None, Header()] = None) -> str:
    if not x_attendant_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-Attendant-Key")
    if x_attendant_key not in _keys():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid X-Attendant-Key")
    return x_attendant_key


Attendant = Annotated[str, Depends(require_attendant)]


class TicketEnterIn(BaseModel):
    plate: str
    now: datetime


class AtIn(BaseModel):
    """Shared body for the pay and exit endpoints: just a timestamp."""

    now: datetime


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    garage_id: int
    plate: str
    entered_at: datetime
    paid_at: datetime | None
    exited_at: datetime | None
    fee: Decimal | None
    status: str


class GarageOut(BaseModel):
    name: str
    capacity: int
    open_count: int


class PassIn(BaseModel):
    plate: str
    months: int
    starts_at: datetime


class PassOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    garage_id: int
    plate: str
    valid_from: datetime
    valid_to: datetime
    price: Decimal


app = FastAPI(title="Parking", version="0.1.0")


@app.post(
    "/garages/{garage_id}/tickets", response_model=TicketOut, status_code=status.HTTP_201_CREATED
)
def enter(garage_id: int, body: TicketEnterIn, _attendant: Attendant, db: DbSession) -> Ticket:
    try:
        return ParkingService(db).enter(garage_id, body.plate, body.now)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except Full as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except AlreadyOpen as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@app.post("/tickets/{ticket_id}/pay", response_model=TicketOut)
def pay(ticket_id: int, body: AtIn, _attendant: Attendant, db: DbSession) -> Ticket:
    try:
        return ParkingService(db).pay(ticket_id, body.now, DEFAULT_CARD)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@app.post("/tickets/{ticket_id}/exit", response_model=TicketOut)
def exit_ticket(ticket_id: int, body: AtIn, _attendant: Attendant, db: DbSession) -> Ticket:
    try:
        return ParkingService(db).exit(ticket_id, body.now, DEFAULT_CARD)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except NotPaid as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@app.get("/garages/{garage_id}", response_model=GarageOut)
def get_garage(garage_id: int, _attendant: Attendant, db: DbSession) -> GarageOut:
    garage = GarageRepo(db).get(garage_id)
    if garage is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "garage not found")
    open_count = TicketRepo(db).open_count(garage_id)
    return GarageOut(name=garage.name, capacity=garage.capacity, open_count=open_count)


@app.post(
    "/garages/{garage_id}/passes", response_model=PassOut, status_code=status.HTTP_201_CREATED
)
def buy_pass(garage_id: int, body: PassIn, _attendant: Attendant, db: DbSession) -> Pass:
    try:
        return ParkingService(db).buy_pass(garage_id, body.plate, body.months, body.starts_at)
    except OverlappingPass as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@app.get("/garages/{garage_id}/passes/{plate}", response_model=PassOut)
def get_active_pass(garage_id: int, plate: str, _attendant: Attendant, db: DbSession) -> Pass:
    now = datetime.now(UTC)
    existing = db.scalars(select(Pass).where(Pass.plate == plate)).first()
    lookup_garage_id = existing.garage_id if existing is not None else garage_id
    active_pass = PassRepo(db).active_for_plate(lookup_garage_id, plate, now)
    if active_pass is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no active pass for this plate")
    return active_pass
