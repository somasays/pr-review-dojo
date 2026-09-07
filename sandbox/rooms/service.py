"""Booking service: business rules layered on the repositories."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sandbox.rooms.db import Booking, Waitlist
from sandbox.rooms.domain.slots import (
    MEMBER_MONTHLY_CREDIT_CENTS,
    Slot,
    price_cents,
    recurring_slots,
    split_credit_cents,
)
from sandbox.rooms.repo import BookingRepo, RoomRepo, WaitlistRepo


def _as_utc(ts: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip even for timezone-aware columns."""
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class NotAllowed(Exception):
    pass


class BookingService:
    def __init__(self, rooms: RoomRepo, bookings: BookingRepo, waitlist: WaitlistRepo) -> None:
        self.rooms = rooms
        self.bookings = bookings
        self.waitlist = waitlist

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

    def book_recurring(
        self, room_id: str, holder_email: str, first_slot: Slot, weeks: int, member: bool
    ) -> list[Booking]:
        """Book the same slot every week for `weeks` weeks, or none of them.

        Every week's slot is checked for a conflict before any booking is
        written. Each `BookingRepo.add` call commits on its own (repositories
        here always do), so committing one booking before every slot in the
        series is confirmed free would leave part of the series booked if a
        later week conflicts.
        """
        room = self.rooms.get(room_id)
        if room is None or not room.active:
            raise NotFound(f"room {room_id!r} not found or inactive")
        slots = recurring_slots(first_slot, weeks)
        for slot in slots:
            if self.bookings.find_conflicts(room_id, slot):
                raise Conflict(f"room {room_id!r} is already booked for {slot.start.isoformat()}")
        credit_cents = MEMBER_MONTHLY_CREDIT_CENTS if member else 0
        shares = split_credit_cents(credit_cents, weeks)
        return [
            self.bookings.add(
                Booking(
                    id=str(uuid.uuid4()),
                    room_id=room_id,
                    holder_email=holder_email,
                    start=slot.start,
                    end=slot.end,
                    price_cents=max(price_cents(slot, room.rate_cents_per_hour, member) - share, 0),
                    created_at=datetime.now(UTC),
                )
            )
            for slot, share in zip(slots, shares, strict=True)
        ]

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
        self._promote_waitlist(cancelled)
        return cancelled

    def join_waitlist(self, room_id: str, holder_email: str, slot: Slot, member: bool) -> Waitlist:
        room = self.rooms.get(room_id)
        if room is None or not room.active:
            raise NotFound(f"room {room_id!r} not found or inactive")
        entry = Waitlist(
            id=str(uuid.uuid4()),
            room_id=room_id,
            holder_email=holder_email,
            start=slot.start,
            end=slot.end,
            member=member,
            created_at=datetime.now(UTC),
        )
        return self.waitlist.add(entry)

    def _promote_waitlist(self, freed: Booking) -> Booking | None:
        """Book the first waiting holder into the slot a cancellation just freed."""
        entry = self.waitlist.first_active_for_slot(
            freed.room_id, _as_utc(freed.start), _as_utc(freed.end)
        )
        if entry is None:
            return None
        room = self.rooms.get(freed.room_id)
        assert room is not None
        slot = Slot(_as_utc(entry.start), _as_utc(entry.end))
        booking = Booking(
            id=str(uuid.uuid4()),
            room_id=freed.room_id,
            holder_email=entry.holder_email,
            start=slot.start,
            end=slot.end,
            price_cents=price_cents(slot, room.rate_cents_per_hour, entry.member),
            created_at=datetime.now(UTC),
        )
        created = self.bookings.add(booking)
        self.waitlist.mark_fulfilled(entry.id)
        return created
