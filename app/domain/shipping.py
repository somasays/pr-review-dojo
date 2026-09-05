"""Shipping transit time helpers."""

from __future__ import annotations

from datetime import date, datetime, timedelta


def transit_days(shipped_at: datetime | None, today: date) -> int:
    """Days between shipment and today, inclusive of both ends."""
    if shipped_at is None:
        return 0
    cur = shipped_at.date()
    days = 0
    while cur <= today:
        days += 1
        cur += timedelta(days=1)
    return days
