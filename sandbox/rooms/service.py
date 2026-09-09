"""Booking service: business rules layered on the repositories."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sandbox.rooms.db import Booking
from sandbox.rooms.domain.slots import Slot, can_amend, price_cents
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

    def amend(
        self, booking_id: str, new_start: datetime, new_end: datetime, member: bool
    ) -> tuple[Booking, int]:
        """Move or extend an active booking to a new slot in the same room.

        Returns the updated booking and the price difference in cents
        (positive if the new slot costs more, negative if it costs less).
        """
        booking = self.bookings.get(booking_id)
        if booking is None or booking.cancelled_at is not None:
            raise NotFound(f"booking {booking_id!r} not found")
        room = self.rooms.get(booking.room_id)
        if room is None or not room.active:
            raise NotFound(f"room {booking.room_id!r} not found or inactive")

        now = datetime.now(UTC)
        current_start = (
            booking.start if booking.start.tzinfo is not None else booking.start.replace(tzinfo=UTC)
        )
        if not can_amend(now, current_start):
            raise NotAllowed("too close to the start of the booking to amend it")

        new_slot = Slot(new_start, new_end)
        new_price = price_cents(new_slot, room.rate_cents_per_hour, member)
        old_price = booking.price_cents

        updated = self.bookings.update_slot(booking_id, new_slot.start, new_slot.end, new_price)
        assert updated is not None

        conflicts = self.bookings.find_conflicts_excluding(booking.room_id, new_slot, booking_id)
        if conflicts:
            raise Conflict(f"room {booking.room_id!r} is already booked for that slot")

        self.bookings.reset_reminder(booking_id)
        return updated, new_price - old_price

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
