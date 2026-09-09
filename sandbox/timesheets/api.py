"""FastAPI app for the timesheets service.

Auth is X-Timesheets-Key: TIMESHEETS_KEYS is a comma-separated list of
"role:email:key" triples, role being worker or manager. Pydantic models
are explicit allowlists, never ORM rows. Every write goes through a
service method, whose unit_of_work() owns the transaction, so these
handlers never commit.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import date, datetime
from decimal import Decimal
from os import environ
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, sessionmaker

from sandbox.timesheets.db import Timesheet, get_session_factory
from sandbox.timesheets.repo import ShiftRepo, TimesheetRepo, WorkerRepo
from sandbox.timesheets.service import (
    InvalidShift,
    NotAllowed,
    NotFound,
    Overlap,
    ShiftService,
    TimesheetService,
)


def _keys() -> dict[str, tuple[str, str]]:
    """Parse TIMESHEETS_KEYS into {key: (role, email)}."""
    raw = environ.get("TIMESHEETS_KEYS", "")
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


def get_shift_service(session_factory: SessionFactoryDep) -> ShiftService:
    return ShiftService(session_factory)


def get_timesheet_service(session_factory: SessionFactoryDep) -> TimesheetService:
    return TimesheetService(session_factory)


ShiftServiceDep = Annotated[ShiftService, Depends(get_shift_service)]
TimesheetServiceDep = Annotated[TimesheetService, Depends(get_timesheet_service)]


def get_identity(x_timesheets_key: Annotated[str | None, Header()] = None) -> tuple[str, str]:
    """The (role, email) for a valid X-Timesheets-Key, of either role."""
    if not x_timesheets_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-Timesheets-Key")
    identity = _keys().get(x_timesheets_key)
    if identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid X-Timesheets-Key")
    return identity


Identity = Annotated[tuple[str, str], Depends(get_identity)]


def require_worker(identity: Identity) -> str:
    role, email = identity
    if role != "worker":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "a worker key is required")
    return email


def require_manager(identity: Identity) -> str:
    role, email = identity
    if role != "manager":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "a manager key is required")
    return email


WorkerEmail = Annotated[str, Depends(require_worker)]
ManagerEmail = Annotated[str, Depends(require_manager)]


class ShiftCreate(BaseModel):
    start_utc: datetime
    end_utc: datetime
    note: str | None = None


class ShiftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timesheet_id: int
    start_utc: datetime
    end_utc: datetime
    minutes: int
    note: str | None
    group_id: int | None


class TimesheetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    worker_id: int
    period_start: date
    version: int
    status: str
    submitted_at: datetime | None
    decided_at: datetime | None
    decided_by: str | None
    total_pay: Decimal | None


class DecisionIn(BaseModel):
    approve: bool
    reason: str | None = None


app = FastAPI(title="Timesheets", version="0.1.0")


@app.post("/shifts", response_model=ShiftOut, status_code=status.HTTP_201_CREATED)
def create_shift(
    body: ShiftCreate, worker_email: WorkerEmail, service: ShiftServiceDep, db: DbSession
):
    try:
        shift = service.record(worker_email, body.start_utc, body.end_utc, body.note)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except NotAllowed as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except Overlap as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except InvalidShift as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    # A shift split at local midnight is stored as two rows; the response
    # reports the total minutes of the whole shift the worker clocked, not
    # just the first part.
    total_minutes = shift.minutes
    if shift.group_id is not None:
        total_minutes = sum(part.minutes for part in ShiftRepo(db).for_group(shift.group_id))
    return ShiftOut(
        id=shift.id,
        timesheet_id=shift.timesheet_id,
        start_utc=shift.start_utc,
        end_utc=body.end_utc,
        minutes=total_minutes,
        note=shift.note,
        group_id=shift.group_id,
    )


@app.post("/timesheets/{period_start}/submit", response_model=TimesheetOut)
def submit_timesheet(period_start: date, worker_email: WorkerEmail, service: TimesheetServiceDep):
    try:
        return service.submit(worker_email, period_start)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except NotAllowed as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@app.get("/timesheets/pending", response_model=list[TimesheetOut])
def list_pending_timesheets(_manager_email: ManagerEmail, db: DbSession) -> Sequence[Timesheet]:
    return TimesheetRepo(db).pending()


@app.get("/timesheets/{timesheet_id}", response_model=TimesheetOut)
def get_timesheet(timesheet_id: int, identity: Identity, db: DbSession) -> Timesheet:
    """The timesheet's own worker, or any manager, may read it."""
    role, email = identity
    timesheet = TimesheetRepo(db).get(timesheet_id)
    if timesheet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timesheet not found")
    if role != "manager":
        owner = WorkerRepo(db).get(timesheet.worker_id)
        if owner is None or owner.email != email:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not allowed to view this timesheet")
    return timesheet


@app.post("/timesheets/{timesheet_id}/decision", response_model=TimesheetOut)
def decide_timesheet(
    timesheet_id: int, body: DecisionIn, manager_email: ManagerEmail, service: TimesheetServiceDep
):
    try:
        return service.decide(manager_email, timesheet_id, body.approve, body.reason)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except NotAllowed as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@app.post(
    "/timesheets/{timesheet_id}/corrections",
    response_model=TimesheetOut,
    status_code=status.HTTP_201_CREATED,
)
def correct_timesheet(timesheet_id: int, worker_email: WorkerEmail, service: TimesheetServiceDep):
    try:
        return service.correct(worker_email, timesheet_id)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except NotAllowed as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
