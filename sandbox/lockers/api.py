"""FastAPI app for the lockers service.

Depositing a parcel and reading the compartment summary require a valid
X-Courier-Key (see LOCKERS_COURIER_KEYS). Pickup is public: the six-digit
code is the authentication. Pydantic models are explicit allowlists, never
ORM rows; naive datetimes serialize as plain ISO strings with no offset.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from os import environ
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from sandbox.lockers.db import get_session_factory
from sandbox.lockers.domain.fit import Dimensions
from sandbox.lockers.repo import CompartmentRepo
from sandbox.lockers.service import DepositService, Expired, InvalidCode, NoSpace, PickupService


def _courier_keys() -> set[str]:
    raw = environ.get("LOCKERS_COURIER_KEYS", "")
    return {key.strip() for key in raw.split(",") if key.strip()}


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


def get_deposit_service(session_factory: SessionFactoryDep) -> DepositService:
    return DepositService(session_factory)


def get_pickup_service(session_factory: SessionFactoryDep) -> PickupService:
    return PickupService(session_factory)


DepositServiceDep = Annotated[DepositService, Depends(get_deposit_service)]
PickupServiceDep = Annotated[PickupService, Depends(get_pickup_service)]


def require_courier(x_courier_key: Annotated[str | None, Header()] = None) -> None:
    if not x_courier_key or x_courier_key not in _courier_keys():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid X-Courier-Key")


class DepositRequest(BaseModel):
    width_cm: int
    height_cm: int
    depth_cm: int
    recipient_email: str


class ParcelOut(BaseModel):
    id: int
    compartment_id: int
    pickup_code: str
    deposited_at: str
    expires_at: str


class PickupRequest(BaseModel):
    code: str


class PickupOut(BaseModel):
    late_fee_cents: int


class CompartmentOut(BaseModel):
    size: str
    total: int
    occupied: int


def _iso(dt: datetime) -> str:
    return dt.isoformat()


app = FastAPI(title="Lockers", version="0.1.0")


@app.post(
    "/lockers/{locker_id}/parcels",
    response_model=ParcelOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_courier)],
)
def deposit_parcel(locker_id: int, body: DepositRequest, service: DepositServiceDep) -> ParcelOut:
    dimensions = Dimensions(body.width_cm, body.height_cm, body.depth_cm)
    try:
        parcel = service.deposit(locker_id, dimensions, body.recipient_email, datetime.utcnow())
    except NoSpace as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return ParcelOut(
        id=parcel.id,
        compartment_id=parcel.compartment_id,
        pickup_code=parcel.pickup_code,
        deposited_at=_iso(parcel.deposited_at),
        expires_at=_iso(parcel.expires_at),
    )


@app.post("/lockers/{locker_id}/pickup", response_model=PickupOut)
def pickup_parcel(locker_id: int, body: PickupRequest, service: PickupServiceDep) -> PickupOut:
    try:
        fee = service.pickup(locker_id, body.code, datetime.utcnow())
    except InvalidCode as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except Expired as exc:
        raise HTTPException(status.HTTP_410_GONE, str(exc)) from exc
    return PickupOut(late_fee_cents=fee)


@app.get(
    "/lockers/{locker_id}/compartments",
    response_model=list[CompartmentOut],
    dependencies=[Depends(require_courier)],
)
def compartment_summary(locker_id: int, db: DbSession) -> list[CompartmentOut]:
    compartments = CompartmentRepo(db).list_by_locker(locker_id)
    counts: dict[str, dict[str, int]] = {}
    for compartment in compartments:
        entry = counts.setdefault(compartment.size, {"total": 0, "occupied": 0})
        entry["total"] += 1
        if compartment.occupied:
            entry["occupied"] += 1
    return [
        CompartmentOut(size=size, total=c["total"], occupied=c["occupied"])
        for size, c in sorted(counts.items())
    ]
