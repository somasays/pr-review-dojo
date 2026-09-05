"""Shipping transit time helpers."""

from __future__ import annotations

from datetime import date, datetime

from app.domain.dates import DateRange


def transit_days(shipped_at: datetime | None, today: date) -> int:
    """Days between shipment and today, inclusive of both ends."""
    if shipped_at is None or shipped_at.date() > today:
        return 0
    return DateRange(shipped_at.date(), today).days
