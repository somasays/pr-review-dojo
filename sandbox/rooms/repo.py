"""Repositories: the only place that builds queries.

Convention (see sandbox/rooms/README.md): every repository method is its own
unit of work and commits before returning. This is the opposite of
app/db/repositories.py, which never commits. Callers here never manage a
transaction themselves.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from sandbox.rooms.db import Booking, Room, Waitlist
from sandbox.rooms.domain.slots import Slot


class RoomRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, room: Room) -> Room:
        self.session.add(room)
        self.session.commit()
        return room

    def get(self, room_id: str) -> Room | None:
        return self.session.get(Room, room_id)


class BookingRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, booking: Booking) -> Booking:
        self.session.add(booking)
        self.session.commit()
        return booking

    def get(self, booking_id: str) -> Booking | None:
        return self.session.get(Booking, booking_id)

    def list_active_for_room(self, room_id: str, day: date) -> Sequence[Booking]:
        """Active bookings that touch the given UTC calendar day."""
        day_start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        stmt = (
            select(Booking)
            .where(
                Booking.room_id == room_id,
                Booking.cancelled_at.is_(None),
                Booking.start < day_end,
                Booking.end > day_start,
            )
            .order_by(Booking.start)
        )
        return self.session.scalars(stmt).all()

    def find_conflicts(self, room_id: str, slot: Slot) -> Sequence[Booking]:
        stmt = select(Booking).where(
            Booking.room_id == room_id,
            Booking.cancelled_at.is_(None),
            Booking.start < slot.end,
            Booking.end > slot.start,
        )
        return self.session.scalars(stmt).all()

    def cancel(self, booking_id: str) -> Booking | None:
        booking = self.session.get(Booking, booking_id)
        if booking is None:
            return None
        booking.cancelled_at = datetime.now(UTC)
        self.session.commit()
        return booking

    def mark_reminded(self, booking_id: str) -> None:
        booking = self.session.get(Booking, booking_id)
        if booking is None:
            return
        booking.reminded_at = datetime.now(UTC)
        self.session.commit()

    def due_for_reminder(self, now: datetime, within: timedelta) -> Sequence[Booking]:
        """Active, un-reminded bookings starting within `within` of `now`."""
        stmt = select(Booking).where(
            Booking.cancelled_at.is_(None),
            Booking.reminded_at.is_(None),
            Booking.start >= now,
            Booking.start <= now + within,
        )
        return self.session.scalars(stmt).all()


class WaitlistRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, entry: Waitlist) -> Waitlist:
        self.session.add(entry)
        self.session.commit()
        return entry

    def list_for_room(self, room_id: str) -> Sequence[Waitlist]:
        """Everyone still waiting for an opening in this room, oldest first."""
        stmt = (
            select(Waitlist)
            .where(Waitlist.room_id == room_id, Waitlist.fulfilled_at.is_(None))
            .order_by(Waitlist.created_at)
        )
        return self.session.scalars(stmt).all()

    def first_active_for_slot(
        self, room_id: str, start: datetime, end: datetime
    ) -> Waitlist | None:
        """The earliest holder still waiting for this exact slot in this room."""
        stmt = (
            select(Waitlist)
            .where(
                Waitlist.room_id == room_id,
                Waitlist.start == start,
                Waitlist.end == end,
                Waitlist.fulfilled_at.is_(None),
            )
            .order_by(Waitlist.created_at)
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def mark_fulfilled(self, entry_id: str) -> Waitlist | None:
        entry = self.session.get(Waitlist, entry_id)
        if entry is None:
            return None
        entry.fulfilled_at = datetime.now(UTC)
        self.session.commit()
        return entry
