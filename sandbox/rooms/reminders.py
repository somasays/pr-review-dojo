"""Reminder job: notify holders of bookings starting soon, exactly once each."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from sandbox.rooms.db import Booking
from sandbox.rooms.repo import BookingRepo

REMINDER_WINDOW = timedelta(minutes=60)

Sender = Callable[[str, Booking], None]


def send_reminders(session_factory: sessionmaker[Session], now: datetime, sender: Sender) -> int:
    """Call `sender(holder_email, booking)` once for each booking due soon.

    A booking is due when it starts within REMINDER_WINDOW of `now` and has
    not already been reminded. Marking `reminded_at` right after the send
    makes a repeat run of this job a no-op for that booking.
    """
    session = session_factory()
    try:
        repo = BookingRepo(session)
        due = repo.due_for_reminder(now, REMINDER_WINDOW)
        for booking in due:
            sender(booking.holder_email, booking)
            repo.mark_reminded(booking.id)
        return len(due)
    finally:
        session.close()
