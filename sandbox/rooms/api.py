"""FastAPI app for the rooms booking service.

Auth is X-API-Key. ROOMS_API_KEYS is a comma-separated list of
"email:key" pairs; a valid key resolves to the holder email it authenticates.
Pydantic request and response models are explicit allowlists, never ORM rows.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from os import environ
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from sandbox.rooms.db import Booking, Waitlist, get_session_factory
from sandbox.rooms.domain.slots import MAX_RECURRING_WEEKS, Slot
from sandbox.rooms.repo import BookingRepo, RoomRepo, WaitlistRepo
from sandbox.rooms.service import BookingService, Conflict, NotAllowed, NotFound


def _api_keys() -> dict[str, str]:
    raw = environ.get("ROOMS_API_KEYS", "")
    keys: dict[str, str] = {}
    for pair in raw.split(","):
        email, _, key = pair.strip().partition(":")
        if email and key:
            keys[key] = email
    return keys


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]


def get_holder_email(x_api_key: Annotated[str | None, Header()] = None) -> str:
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-API-Key")
    email = _api_keys().get(x_api_key)
    if email is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")
    return email


HolderEmail = Annotated[str, Depends(get_holder_email)]


def get_service(db: DbSession) -> BookingService:
    return BookingService(RoomRepo(db), BookingRepo(db), WaitlistRepo(db))


Service = Annotated[BookingService, Depends(get_service)]


class BookingCreate(BaseModel):
    room_id: str
    start: datetime
    end: datetime
    member: bool = False


class RecurringBookingCreate(BaseModel):
    room_id: str
    start: datetime
    end: datetime
    weeks: Annotated[int, Field(ge=1, le=MAX_RECURRING_WEEKS)]
    member: bool = False


class WaitlistJoin(BaseModel):
    start: datetime
    end: datetime
    member: bool = False


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    room_id: str
    holder_email: str
    start: datetime
    end: datetime
    price_cents: int
    cancelled_at: datetime | None


class WaitlistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    start: datetime
    end: datetime
    created_at: datetime


class FreeSlotOut(BaseModel):
    start: datetime
    end: datetime


class AvailabilityOut(BaseModel):
    room_id: str
    day: date
    free_slots: list[FreeSlotOut]


def _as_utc(ts: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip even for timezone-aware columns."""
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def _free_half_hours(day: date, booked: Sequence[Booking]) -> list[FreeSlotOut]:
    day_start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    booked_utc = [(_as_utc(b.start), _as_utc(b.end)) for b in booked]
    free = []
    for i in range(48):
        start = day_start + timedelta(minutes=30 * i)
        end = start + timedelta(minutes=30)
        if not any(b_start < end and start < b_end for b_start, b_end in booked_utc):
            free.append(FreeSlotOut(start=start, end=end))
    return free


app = FastAPI(title="Rooms", version="0.1.0")


def _reraise_as_http(exc: NotFound | Conflict) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@app.post("/bookings", response_model=BookingOut, status_code=status.HTTP_201_CREATED)
def create_booking(body: BookingCreate, holder_email: HolderEmail, service: Service) -> Booking:
    slot = Slot(body.start, body.end)
    try:
        return service.book(body.room_id, holder_email, slot, body.member)
    except (NotFound, Conflict) as exc:
        raise _reraise_as_http(exc) from exc


@app.post(
    "/bookings/recurring", response_model=list[BookingOut], status_code=status.HTTP_201_CREATED
)
def create_recurring_booking(
    body: RecurringBookingCreate, holder_email: HolderEmail, service: Service
) -> list[Booking]:
    first_slot = Slot(body.start, body.end)
    try:
        return service.book_recurring(
            body.room_id, holder_email, first_slot, body.weeks, body.member
        )
    except (NotFound, Conflict) as exc:
        raise _reraise_as_http(exc) from exc


@app.get("/bookings/{booking_id}", response_model=BookingOut)
def get_booking(booking_id: str, _holder_email: HolderEmail, db: DbSession) -> Booking:
    booking = BookingRepo(db).get(booking_id)
    if booking is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "booking not found")
    return booking


@app.delete("/bookings/{booking_id}", response_model=BookingOut)
def cancel_booking(booking_id: str, holder_email: HolderEmail, service: Service) -> Booking:
    try:
        return service.cancel(booking_id, holder_email)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except NotAllowed as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@app.post(
    "/rooms/{room_id}/waitlist", response_model=WaitlistOut, status_code=status.HTTP_201_CREATED
)
def join_waitlist(
    room_id: str, body: WaitlistJoin, holder_email: HolderEmail, service: Service
) -> Waitlist:
    slot = Slot(body.start, body.end)
    try:
        return service.join_waitlist(room_id, holder_email, slot, body.member)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@app.get("/rooms/{room_id}/waitlist", response_model=list[WaitlistOut])
def get_room_waitlist(
    room_id: str, _holder_email: HolderEmail, db: DbSession
) -> Sequence[Waitlist]:
    return WaitlistRepo(db).list_for_room(room_id)


@app.get("/rooms/{room_id}/availability", response_model=AvailabilityOut)
def get_availability(
    room_id: str, day: Annotated[date, Query()], _holder_email: HolderEmail, db: DbSession
) -> AvailabilityOut:
    room = RoomRepo(db).get(room_id)
    if room is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "room not found")
    booked = BookingRepo(db).list_active_for_room(room_id, day)
    return AvailabilityOut(room_id=room_id, day=day, free_slots=_free_half_hours(day, booked))
