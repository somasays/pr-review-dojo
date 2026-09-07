"""Booking service: business rules layered on the repositories."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sandbox.rooms.db import Booking
from sandbox.rooms.domain.slots import Slot, price_cents
from sandbox.rooms.repo import BookingRepo, RoomRepo


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class NotAllowed(Exception):
    pass


class BookingService:
    def __init__(self, rooms: RoomRepo, bookings: BookingRepo) -> None:
        self.rooms = rooms
        self.bookings = bookings

    def book(self, room_id: str, holder_email: str, slot: Slot, member: bool) -> Booking:
        room = self.rooms.get(room_id)
        if room is None or not room.active:
            raise NotFound(f"room {room_id!r} not found or inactive")
        if self.bookings.find_conflicts(room_id, slot):
            raise Conflict(f"room {room_id!r} is already booked for that slot")
        booking = Booking(
            id=str(uuid.uuid4()),
            room_id=room_id,
            holder_email=holder_email,
            start=slot.start,
            end=slot.end,
            price_cents=price_cents(slot, room.rate_cents_per_hour, member),
            created_at=datetime.now(UTC),
        )
        return self.bookings.add(booking)

    def cancel(self, booking_id: str, holder_email: str) -> Booking:
        booking = self.bookings.get(booking_id)
        if booking is None:
            raise NotFound(f"booking {booking_id!r} not found")
        if booking.holder_email != holder_email:
            raise NotAllowed("only the holder can cancel this booking")
        if booking.cancelled_at is not None:
            return booking
        cancelled = self.bookings.cancel(booking_id)
        assert cancelled is not None
        return cancelled
