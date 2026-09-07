"""Tests for sandbox/rooms/service.py."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from sandbox.rooms.db import Room
from sandbox.rooms.domain.slots import Slot
from sandbox.rooms.repo import BookingRepo, RoomRepo
from sandbox.rooms.service import BookingService, Conflict, NotAllowed, NotFound


def _slot(start_hour: int, end_hour: int, start_minute: int = 0, end_minute: int = 0) -> Slot:
    return Slot(
        datetime(2026, 9, 8, start_hour, start_minute, tzinfo=UTC),
        datetime(2026, 9, 8, end_hour, end_minute, tzinfo=UTC),
    )


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
