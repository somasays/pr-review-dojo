"""Tests for sandbox/rooms/reminders.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from sandbox.rooms.db import Room
from sandbox.rooms.domain.slots import Slot
from sandbox.rooms.reminders import send_reminders
from sandbox.rooms.repo import BookingRepo, RoomRepo, WaitlistRepo
from sandbox.rooms.service import BookingService

NOW = datetime(2026, 9, 8, 8, 30, tzinfo=UTC)


def _book_starting_in(service: BookingService, room: Room, minutes: int) -> str:
    """Book a half-hour slot, rounded down to the half hour, `minutes` from NOW."""
    start = NOW + timedelta(minutes=minutes)
    start = start.replace(minute=(start.minute // 30) * 30, second=0, microsecond=0)
    booking = service.book(
        room.id, "ada@example.com", Slot(start, start + timedelta(minutes=30)), member=False
    )
    return booking.id


def test_send_reminders_sends_only_bookings_in_window(
    session_factory: sessionmaker[Session], db: Session, room: Room
) -> None:
    service = BookingService(RoomRepo(db), BookingRepo(db), WaitlistRepo(db))
    soon_id = _book_starting_in(service, room, 30)
    later_id = _book_starting_in(service, room, 180)

    sent: list[str] = []
    count = send_reminders(session_factory, NOW, lambda email, booking: sent.append(booking.id))

    assert count == 1
    assert sent == [soon_id]
    assert later_id not in sent


def test_send_reminders_is_idempotent(
    session_factory: sessionmaker[Session], db: Session, room: Room
) -> None:
    service = BookingService(RoomRepo(db), BookingRepo(db), WaitlistRepo(db))
    _book_starting_in(service, room, 30)

    sent: list[str] = []
    send_reminders(session_factory, NOW, lambda email, booking: sent.append(booking.id))
    second_count = send_reminders(
        session_factory, NOW, lambda email, booking: sent.append(booking.id)
    )

    assert second_count == 0
    assert len(sent) == 1
