"""Fit and pricing math: pure logic with no IO.

See sandbox/lockers/README.md for why this package's conventions (integer
cents, naive UTC datetimes) differ from app/domain and sandbox/rooms/domain.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum


class Size(Enum):
    S = "S"
    M = "M"
    L = "L"


# Max width, height, depth (cm) a compartment of each size accepts. A parcel
# is never rotated to try to fit a smaller size; each of its edges must be no
# larger than the matching limit.
_SIZE_LIMITS_CM: dict[Size, tuple[int, int, int]] = {
    Size.S: (20, 20, 20),
    Size.M: (40, 40, 40),
    Size.L: (60, 60, 60),
}


@dataclass(frozen=True, slots=True)
class Dimensions:
    """A parcel's dimensions in centimeters."""

    w: int
    h: int
    d: int

    def __post_init__(self) -> None:
        if self.w <= 0 or self.h <= 0 or self.d <= 0:
            raise ValueError("dimensions must be positive")


def fits(dimensions: Dimensions, size: Size) -> bool:
    """True if the parcel fits in a compartment of the given size."""
    limit_w, limit_h, limit_d = _SIZE_LIMITS_CM[size]
    return dimensions.w <= limit_w and dimensions.h <= limit_h and dimensions.d <= limit_d


def smallest_size_for(dimensions: Dimensions) -> Size | None:
    """The smallest size the parcel fits in, or None if it fits none."""
    for size in Size:
        if fits(dimensions, size):
            return size
    return None


def late_fee_cents(
    deposited_at: datetime,
    picked_up_at: datetime,
    grace_hours: int,
    fee_cents_per_hour: int,
    cap_cents: int,
) -> int:
    """Late fee for picking up a parcel after its grace period.

    The grace period runs from `deposited_at` for `grace_hours` hours; a
    pickup during or exactly at the end of it costs nothing. After that,
    hours late are rounded up: any part of an hour late is billed as a full
    hour (a pickup one minute past grace already owes one hour's fee). The
    total is capped at `cap_cents`.
    """
    if picked_up_at < deposited_at:
        raise ValueError("picked_up_at must not be before deposited_at")
    grace_ends = deposited_at + timedelta(hours=grace_hours)
    if picked_up_at <= grace_ends:
        return 0
    late_seconds = (picked_up_at - grace_ends).total_seconds()
    late_hours = -(-int(late_seconds) // 3600)  # ceil division, no float math
    return min(late_hours * fee_cents_per_hour, cap_cents)


def expires_at(deposited_at: datetime, hold_hours: int) -> datetime:
    """When a parcel expires if it is never picked up."""
    return deposited_at + timedelta(hours=hold_hours)


def can_redirect(now: datetime, deadline: datetime) -> bool:
    """True if a parcel can still be redirected to another locker.

    Uses the same boundary PickupService uses for a normal pickup: right at
    the moment of expiry is still in time, so a parcel that could still be
    picked up can still be redirected too.
    """
    return now <= deadline
