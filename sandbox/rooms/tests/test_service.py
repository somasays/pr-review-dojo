"""Tests for sandbox/rooms/service.py."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from sandbox.rooms.db import Booking, Room
from sandbox.rooms.domain.slots import Slot
from sandbox.rooms.repo import BookingRepo, RoomRepo
from sandbox.rooms.service import BookingService, Conflict, NotAllowed, NotFound


def _slot(start_hour: int, end_hour: int, start_minute: int = 0, end_minute: int = 0) -> Slot:
    return Slot(
        datetime(2026, 9, 8, start_hour, start_minute, tzinfo=UTC),
        datetime(2026, 9, 8, end_hour, end_minute, tzinfo=UTC),
    )


def _next_half_hour(dt: datetime) -> datetime:
    """The first half-hour-aligned instant strictly after `dt`."""
    aligned = dt.replace(minute=0, second=0, microsecond=0)
    while aligned <= dt:
        aligned += timedelta(minutes=30)
    return aligned


@pytest.fixture
def service(db: Session) -> BookingService:
    return BookingService(RoomRepo(db), BookingRepo(db))


def test_book_creates_booking(service: BookingService, room: Room) -> None:
    booking = service.book(room.id, "ada@example.com", _slot(9, 10), member=False)
    assert booking.room_id == room.id
    assert booking.price_cents == 2000
    assert booking.cancelled_at is None


def test_book_unknown_or_inactive_room_raises_not_found(
    service: BookingService, room: Room, db: Session
) -> None:
    with pytest.raises(NotFound):
        service.book("no-such-room", "ada@example.com", _slot(9, 10), member=False)

    room.active = False
    db.commit()
    with pytest.raises(NotFound):
        service.book(room.id, "ada@example.com", _slot(9, 10), member=False)


def test_book_conflict_and_adjacent_slots(service: BookingService, room: Room) -> None:
    service.book(room.id, "ada@example.com", _slot(9, 10), member=False)
    with pytest.raises(Conflict):
        service.book(
            room.id, "bea@example.com", _slot(9, 10, start_minute=30, end_minute=30), member=False
        )
    # Adjacent, non-overlapping slot on the same room is fine.
    second = service.book(room.id, "bea@example.com", _slot(10, 11), member=False)
    assert second.room_id == room.id


def test_cancel_by_holder_succeeds_and_is_idempotent(service: BookingService, room: Room) -> None:
    booking = service.book(room.id, "ada@example.com", _slot(9, 10), member=False)
    first = service.cancel(booking.id, "ada@example.com")
    assert first.cancelled_at is not None
    second = service.cancel(booking.id, "ada@example.com")
    assert second.cancelled_at == first.cancelled_at


def test_cancel_by_non_holder_raises_not_allowed(service: BookingService, room: Room) -> None:
    booking = service.book(room.id, "ada@example.com", _slot(9, 10), member=False)
    with pytest.raises(NotAllowed):
        service.cancel(booking.id, "eve@example.com")


def test_cancel_unknown_booking_raises_not_found(service: BookingService) -> None:
    with pytest.raises(NotFound):
        service.cancel("no-such-booking", "ada@example.com")


def test_amend_moves_booking_recomputes_price_and_resets_reminder(
    service: BookingService, room: Room, db: Session
) -> None:
    now = datetime.now(UTC)
    original_start = _next_half_hour(now + timedelta(hours=3))
    booking = service.book(
        room.id,
        "ada@example.com",
        Slot(original_start, original_start + timedelta(hours=1)),
        member=False,
    )
    booking.reminded_at = now
    db.commit()

    new_start = _next_half_hour(now + timedelta(hours=5))
    new_slot = Slot(new_start, new_start + timedelta(hours=2))
    updated, price_difference_cents = service.amend(
        booking.id, "ada@example.com", new_slot, member=False
    )

    assert updated.start == new_start
    assert updated.end == new_start + timedelta(hours=2)
    assert updated.price_cents == 4000
    assert price_difference_cents == 2000
    assert updated.reminded_at is None


def test_amend_conflict_with_another_booking_raises(service: BookingService, room: Room) -> None:
    now = datetime.now(UTC)
    start_a = _next_half_hour(now + timedelta(hours=3))
    booking_a = service.book(
        room.id, "ada@example.com", Slot(start_a, start_a + timedelta(minutes=30)), member=False
    )
    start_b = _next_half_hour(now + timedelta(hours=5))
    service.book(
        room.id, "bea@example.com", Slot(start_b, start_b + timedelta(minutes=30)), member=False
    )

    new_slot = Slot(start_b, start_b + timedelta(minutes=30))
    with pytest.raises(Conflict):
        service.amend(booking_a.id, "ada@example.com", new_slot, member=False)


def test_amend_refused_minutes_before_start(
    service: BookingService, room: Room, db: Session
) -> None:
    now = datetime.now(UTC)
    booking = Booking(
        id=str(uuid.uuid4()),
        room_id=room.id,
        holder_email="ada@example.com",
        start=now + timedelta(minutes=5),
        end=now + timedelta(minutes=35),
        price_cents=1000,
        created_at=now,
    )
    db.add(booking)
    db.commit()

    new_start = _next_half_hour(now + timedelta(hours=4))
    new_slot = Slot(new_start, new_start + timedelta(minutes=30))
    with pytest.raises(NotAllowed):
        service.amend(booking.id, "ada@example.com", new_slot, member=False)


def test_amend_refused_once_the_booking_has_started(
    service: BookingService, room: Room, db: Session
) -> None:
    now = datetime.now(UTC)
    booking = Booking(
        id=str(uuid.uuid4()),
        room_id=room.id,
        holder_email="ada@example.com",
        start=now - timedelta(minutes=5),
        end=now + timedelta(minutes=25),
        price_cents=1000,
        created_at=now,
    )
    db.add(booking)
    db.commit()

    new_start = _next_half_hour(now + timedelta(hours=4))
    new_slot = Slot(new_start, new_start + timedelta(minutes=30))
    with pytest.raises(NotAllowed):
        service.amend(booking.id, "ada@example.com", new_slot, member=False)


def test_amend_unknown_booking_raises_not_found(service: BookingService) -> None:
    now = datetime.now(UTC)
    new_start = _next_half_hour(now + timedelta(hours=4))
    new_slot = Slot(new_start, new_start + timedelta(minutes=30))
    with pytest.raises(NotFound):
        service.amend("no-such-booking", "ada@example.com", new_slot, member=False)
