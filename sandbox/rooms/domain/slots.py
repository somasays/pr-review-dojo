"""Slot math: pure logic with no IO.

A slot is a half-open time range that must be timezone-aware UTC and aligned
to the half hour on both ends. See sandbox/rooms/README.md for why this
package's conventions differ from app/domain.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

HALF_HOUR = timedelta(minutes=30)
MAX_DURATION = timedelta(hours=8)
MEMBER_DISCOUNT_PERCENT = 15
MIN_CHARGE_CENTS = 500
AMEND_CUTOFF = timedelta(minutes=30)


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


def can_amend(now: datetime, current_start: datetime) -> bool:
    """True if a booking starting at `current_start` may still be amended at `now`.

    Refused once the booking has started, and refused once fewer than
    AMEND_CUTOFF remain before the start.
    """
    return current_start - now > AMEND_CUTOFF
