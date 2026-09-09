"""Overtime, night differential, and pay math, plus timesheet status: pure
logic with no IO.

See sandbox/timesheets/README.md for why this package's conventions
(integer primary keys, integer-minute durations, Decimal money and rates,
local-timezone pay rules over UTC-stored clock times) differ from app/domain
and the other sandboxes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from zoneinfo import ZoneInfo

_CENTS = Decimal("0.01")
_MINUTES_PER_DAY = 24 * 60
_MINUTES_PER_HOUR = 60


@dataclass(frozen=True, slots=True)
class Rules:
    daily_overtime_after_minutes: int
    weekly_overtime_after_minutes: int
    overtime_multiplier: Decimal
    night_start_hour: int
    night_end_hour: int
    night_differential: Decimal


def _ensure_aware(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("expected a timezone-aware datetime, got a naive one")


def local_day(shift_start_utc: datetime, tz: str) -> date:
    """The local calendar day `shift_start_utc` falls on in `tz`."""
    _ensure_aware(shift_start_utc)
    return shift_start_utc.astimezone(ZoneInfo(tz)).date()


def shift_minutes(start: datetime, end: datetime) -> int:
    """Whole minutes between `start` and `end`, rejecting a non-positive,
    over-24h, or fractional-minute duration."""
    _ensure_aware(start)
    _ensure_aware(end)
    total_seconds = (end - start).total_seconds()
    if total_seconds <= 0:
        raise ValueError("a shift must end after it starts")
    minutes = total_seconds / _MINUTES_PER_HOUR
    if minutes > _MINUTES_PER_DAY:
        raise ValueError("a shift cannot exceed 24 hours")
    if minutes != int(minutes):
        raise ValueError("a shift's length must be a whole number of minutes")
    return int(minutes)


def local_midnight_after(start_utc: datetime, tz: str) -> datetime:
    """The UTC instant of the next local midnight strictly after
    `start_utc`, in `tz`. This is where a shift that crosses the worker's
    local midnight must be split so each part belongs to its own local
    day."""
    _ensure_aware(start_utc)
    zone = ZoneInfo(tz)
    start_local = start_utc.astimezone(zone)
    next_day = start_local.date() + timedelta(days=1)
    midnight_local = datetime(next_day.year, next_day.month, next_day.day, tzinfo=zone)
    return midnight_local.astimezone(UTC)


def _night_window_duration_hours(rules: Rules) -> int:
    duration = (rules.night_end_hour - rules.night_start_hour) % 24
    return duration or 24


def night_minutes(start: datetime, end: datetime, tz: str, rules: Rules) -> int:
    """Minutes of `[start, end)` inside the nightly window (`night_start_hour`
    to `night_end_hour`, wrapping past midnight), evaluated in local time. The
    window recurs daily, so a shift crossing midnight can overlap it twice."""
    zone = ZoneInfo(tz)
    start_local = start.astimezone(zone)
    end_local = end.astimezone(zone)
    duration_hours = _night_window_duration_hours(rules)

    total = 0
    day = start_local.date() - timedelta(days=1)
    last_day = end_local.date()
    while day <= last_day:
        window_start = datetime(day.year, day.month, day.day, rules.night_start_hour, tzinfo=zone)
        window_end = window_start + timedelta(hours=duration_hours)
        overlap_start = max(start_local, window_start)
        overlap_end = min(end_local, window_end)
        if overlap_end > overlap_start:
            total += int((overlap_end - overlap_start).total_seconds() // _MINUTES_PER_HOUR)
        day += timedelta(days=1)
    return total


def split_overtime(day_minutes_so_far: int, shift_minutes: int, rules: Rules) -> tuple[int, int]:
    """Split one shift's minutes into (regular, overtime) given how many
    minutes the worker already logged on that local day. Minutes already
    past `daily_overtime_after_minutes` before this shift started are
    entirely overtime."""
    if day_minutes_so_far < 0 or shift_minutes < 0:
        raise ValueError("minutes must not be negative")
    threshold = rules.daily_overtime_after_minutes
    if day_minutes_so_far >= threshold:
        return 0, shift_minutes
    remaining_regular = threshold - day_minutes_so_far
    if shift_minutes <= remaining_regular:
        return shift_minutes, 0
    return remaining_regular, shift_minutes - remaining_regular


def weekly_overtime(total_regular_minutes_in_period: int, rules: Rules) -> tuple[int, int]:
    """Split a pay period's daily-regular minutes into (regular, overtime)
    against the weekly threshold. Applied after `split_overtime` has already
    pulled out daily overtime."""
    if total_regular_minutes_in_period < 0:
        raise ValueError("minutes must not be negative")
    threshold = rules.weekly_overtime_after_minutes
    if total_regular_minutes_in_period <= threshold:
        return total_regular_minutes_in_period, 0
    return threshold, total_regular_minutes_in_period - threshold


def pay_cents(
    regular_minutes: int,
    overtime_minutes: int,
    night_minutes: int,
    rate: Decimal,
    rules: Rules,
) -> Decimal:
    """Total pay, quantized to cents, rounding half up. Overtime minutes pay
    `rate * overtime_multiplier`; night minutes additionally earn
    `rate * night_differential` on top of their regular or overtime pay."""
    if regular_minutes < 0 or overtime_minutes < 0 or night_minutes < 0:
        raise ValueError("minutes must not be negative")
    hour = Decimal(_MINUTES_PER_HOUR)
    total = (
        Decimal(regular_minutes) / hour * rate
        + Decimal(overtime_minutes) / hour * rate * rules.overtime_multiplier
        + Decimal(night_minutes) / hour * rate * rules.night_differential
    )
    return total.quantize(_CENTS, rounding=ROUND_HALF_UP)


class TimesheetStatus(Enum):
    OPEN = "open"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"


class InvalidTransition(Exception):
    pass


# The only moves a timesheet's status is allowed to make. Approved and
# rejected are terminal here: a correction after approval creates a new
# timesheet version at "open" rather than transitioning this one.
_ALLOWED_TRANSITIONS: dict[TimesheetStatus, frozenset[TimesheetStatus]] = {
    TimesheetStatus.OPEN: frozenset({TimesheetStatus.SUBMITTED}),
    TimesheetStatus.SUBMITTED: frozenset({TimesheetStatus.APPROVED, TimesheetStatus.REJECTED}),
    TimesheetStatus.APPROVED: frozenset(),
    TimesheetStatus.REJECTED: frozenset(),
}


def transition(current: TimesheetStatus, target: TimesheetStatus) -> TimesheetStatus:
    """The only way a timesheet's status may change. Returns `target` when
    the move is legal, raises InvalidTransition otherwise."""
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(f"cannot move a timesheet from {current.value} to {target.value}")
    return target
