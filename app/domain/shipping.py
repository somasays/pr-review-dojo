"""Shipping transit time helpers."""

from __future__ import annotations

from datetime import date, timedelta

from app.db.models import Order


def transit_days(order: Order, today: date) -> int:
    """Days between shipment and today, inclusive of both ends."""
    if order.shipped_at is None:
        return 0
    cur = order.shipped_at.date()
    days = 0
    while cur <= today:
        days += 1
        cur += timedelta(days=1)
    return days
