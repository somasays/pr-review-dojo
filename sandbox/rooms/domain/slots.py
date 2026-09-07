"""Slot math: pure logic with no IO.

A slot is a half-open time range that must be timezone-aware UTC and aligned
to the half hour on both ends. See sandbox/rooms/README.md for why this
package's conventions differ from app/domain.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

HALF_HOUR = timedelta(minutes=30)
WEEK = timedelta(days=7)
MAX_DURATION = timedelta(hours=8)
MEMBER_DISCOUNT_PERCENT = 15
MIN_CHARGE_CENTS = 500
MAX_RECURRING_WEEKS = 8
MEMBER_MONTHLY_CREDIT_CENTS = 1000


def _is_half_hour_aligned(ts: datetime) -> bool:
    return ts.minute % 30 == 0 and ts.second == 0 and ts.microsecond == 0


@dataclass(frozen=True, slots=True)
class Slot:
    """A booking time range. Always constructed as timezone-aware UTC."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("slot times must be timezone-aware")
        start = self.start.astimezone(UTC)
        end = self.end.astimezone(UTC)
        if not _is_half_hour_aligned(start) or not _is_half_hour_aligned(end):
            raise ValueError("slot times must align to the half hour")
        if end <= start:
            raise ValueError(f"end {end} is not after start {start}")
        if end - start > MAX_DURATION:
            raise ValueError(f"slot of {end - start} exceeds the {MAX_DURATION} maximum")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def overlaps(self, other: Slot) -> bool:
        return self.start < other.end and other.start < self.end

    def duration_minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)

    def split_into_half_hours(self) -> list[Slot]:
        """Break this slot into consecutive 30-minute slots."""
        out = []
        cur = self.start
        while cur < self.end:
            nxt = cur + HALF_HOUR
            out.append(Slot(cur, nxt))
            cur = nxt
        return out


def price_cents(slot: Slot, rate_cents_per_hour: int, member: bool) -> int:
    """Price a slot in integer cents.

    Members get a flat discount off the metered rate. The result is never
    below the minimum charge, whatever the discount and duration work out to.
    """
    if rate_cents_per_hour < 0:
        raise ValueError("rate_cents_per_hour must not be negative")
    metered = rate_cents_per_hour * slot.duration_minutes() // 60
    if member:
        metered = metered * (100 - MEMBER_DISCOUNT_PERCENT) // 100
    return max(metered, MIN_CHARGE_CENTS)


def recurring_slots(first: Slot, weeks: int) -> list[Slot]:
    """Return `weeks` slots at the same time of day, one week apart.

    `first` is the first occurrence; every later one shifts by exactly 7
    days so it lands on the same weekday and time.
    """
    if not 1 <= weeks <= MAX_RECURRING_WEEKS:
        raise ValueError(f"weeks must be between 1 and {MAX_RECURRING_WEEKS}")
    return [Slot(first.start + WEEK * i, first.end + WEEK * i) for i in range(weeks)]


def split_credit_cents(credit_cents: int, count: int) -> list[int]:
    """Split a member's monthly credit evenly across `count` bookings.

    Every booking in a recurring series gets an equal share of the credit,
    so a member does not have to redeem it one booking at a time.
    """
    if credit_cents < 0:
        raise ValueError("credit_cents must not be negative")
    if count < 1:
        raise ValueError("count must be at least 1")
    share = int(Decimal(credit_cents) / count)
    return [share] * count
