"""FastAPI app for the metering service.

Auth is X-Metering-Key: METERING_KEYS is a comma-separated list of
"role:email:key" triples, reader (submits/corrects readings, bills) or
customer (reads their own bills). Pydantic models are allowlists, never
ORM rows. `get_db` owns the transaction through `db.session_scope()`.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import date, datetime
from decimal import Decimal
from os import environ
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from sandbox.metering.db import Bill, Reading, get_session_factory, session_scope
from sandbox.metering.domain.tariff import Band, Tariff
from sandbox.metering.repo import AccountRepo, BillRepo, MeterRepo, ReadingRepo
from sandbox.metering.service import (
    BillingService,
    InvalidReading,
    NotAllowed,
    NotFound,
    ReadingService,
)

# A single default tariff. This sandbox has no tariff-management endpoint;
# billing always uses this one.
DEFAULT_TARIFF = Tariff(
    standing_charge_per_day=Decimal("0.25"),
    bands=(
        Band(Decimal("200.000"), Decimal("0.28")),
        Band(Decimal("500.000"), Decimal("0.22")),
        Band(None, Decimal("0.18")),
    ),
)


def _keys() -> dict[str, tuple[str, str]]:
    """Parse METERING_KEYS into {key: (role, email)}."""
    raw = environ.get("METERING_KEYS", "")
    keys: dict[str, tuple[str, str]] = {}
    for triple in raw.split(","):
        role, _, rest = triple.strip().partition(":")
        email, _, key = rest.partition(":")
        if role and email and key:
            keys[key] = (role, email)
    return keys


def get_db() -> Iterator[Session]:
    with session_scope(get_session_factory()) as session:
        yield session


DbSession = Annotated[Session, Depends(get_db)]


def get_identity(x_metering_key: Annotated[str | None, Header()] = None) -> tuple[str, str]:
    """The (role, email) for a valid X-Metering-Key, of either role."""
    if not x_metering_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-Metering-Key")
    identity = _keys().get(x_metering_key)
    if identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid X-Metering-Key")
    return identity


Identity = Annotated[tuple[str, str], Depends(get_identity)]


def require_reader(identity: Identity) -> str:
    role, email = identity
    if role != "reader":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "a reader key is required")
    return email


ReaderEmail = Annotated[str, Depends(require_reader)]


class ReadingIn(BaseModel):
    meter_serial: str
    taken_at: datetime
    value_kwh: Decimal
    source: str = "actual"


class CorrectionIn(BaseModel):
    value_kwh: Decimal


class ReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    meter_id: int
    taken_at: datetime
    value_kwh: Decimal
    source: str
    supersedes_id: int | None


class BillPeriodIn(BaseModel):
    period_start: date
    period_end: date


class BillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    period_start: date
    period_end: date
    kwh: Decimal
    amount: Decimal
    generated_at: datetime


app = FastAPI(title="Metering", version="0.1.0")


@app.post("/readings", response_model=ReadingOut, status_code=status.HTTP_201_CREATED)
def submit_reading(body: ReadingIn, reader_email: ReaderEmail, db: DbSession) -> Reading:
    try:
        return ReadingService(db).submit(
            reader_email, body.meter_serial, body.taken_at, body.value_kwh, body.source
        )
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InvalidReading as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@app.post("/readings/{reading_id}/corrections", response_model=ReadingOut)
def correct_reading(
    reading_id: int, body: CorrectionIn, reader_email: ReaderEmail, db: DbSession
) -> Reading:
    try:
        return ReadingService(db).correct(reader_email, reading_id, body.value_kwh)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except NotAllowed as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@app.post(
    "/accounts/{account_id}/bills", response_model=BillOut, status_code=status.HTTP_201_CREATED
)
def generate_bill(
    account_id: int, body: BillPeriodIn, _reader_email: ReaderEmail, db: DbSession
) -> Bill:
    account = AccountRepo(db).get(account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "account not found")
    try:
        return BillingService(db).generate(
            account.email, body.period_start, body.period_end, DEFAULT_TARIFF
        )
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InvalidReading as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@app.get("/accounts/{account_id}/bills", response_model=list[BillOut])
def list_bills(account_id: int, identity: Identity, db: DbSession) -> Sequence[Bill]:
    """The account's own customer, or any reader, may list its bills."""
    role, email = identity
    account = AccountRepo(db).get(account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "account not found")
    if role == "customer" and account.email != email:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not allowed to view this account's bills")
    return BillRepo(db).for_account(account_id)


@app.get("/meters/{serial}/latest", response_model=ReadingOut)
def latest_reading(serial: str, _reader_email: ReaderEmail, db: DbSession) -> Reading:
    meter = MeterRepo(db).by_serial(serial)
    if meter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "meter not found")
    reading = ReadingRepo(db).latest(meter.id)
    if reading is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no readings for this meter")
    return reading
