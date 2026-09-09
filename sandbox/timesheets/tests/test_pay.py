"""Tests for the pure pay math and status machine in domain/pay.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from sandbox.timesheets.domain.pay import (
    InvalidTransition,
    Rules,
    TimesheetStatus,
    local_day,
    night_minutes,
    pay_cents,
    shift_minutes,
    split_overtime,
    transition,
    weekly_overtime,
)


@pytest.fixture
def rules() -> Rules:
    return Rules(
        daily_overtime_after_minutes=8 * 60,
        weekly_overtime_after_minutes=40 * 60,
        overtime_multiplier=Decimal("1.5"),
        night_start_hour=22,
        night_end_hour=6,
        night_differential=Decimal("0.10"),
    )


def test_local_day_crosses_midnight_against_utc_date() -> None:
    # 06:30 UTC on Jan 2 is 22:30 on Jan 1 in Los Angeles (UTC-8, no DST in January).
    start = datetime(2026, 1, 2, 6, 30, tzinfo=UTC)
    assert local_day(start, "America/Los_Angeles") == datetime(2026, 1, 1).date()


def test_shift_minutes_rejects_non_positive_over_24h_and_fractional_durations() -> None:
    start = datetime(2026, 1, 1, 8, tzinfo=UTC)
    with pytest.raises(ValueError, match="end after it starts"):
        shift_minutes(start, start)
    with pytest.raises(ValueError, match="24 hours"):
        shift_minutes(start, start + timedelta(hours=25))
    with pytest.raises(ValueError, match="whole number of minutes"):
        shift_minutes(start, start + timedelta(seconds=30))


def test_night_minutes_crosses_midnight_inside_one_window(rules: Rules) -> None:
    # 23:30 to 01:30 falls entirely within the 22:00-06:00 night window.
    start = datetime(2026, 1, 1, 23, 30, tzinfo=UTC)
    end = datetime(2026, 1, 2, 1, 30, tzinfo=UTC)
    assert night_minutes(start, end, "UTC", rules) == 120


def test_night_minutes_partial_overlap_on_the_morning_side(rules: Rules) -> None:
    # 05:00 to 07:00 overlaps the tail of the previous night's window (05:00-06:00).
    start = datetime(2026, 1, 1, 5, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 7, 0, tzinfo=UTC)
    assert night_minutes(start, end, "UTC", rules) == 60


def test_split_overtime_at_the_daily_boundary(rules: Rules) -> None:
    # Exactly at the threshold: no overtime yet.
    assert split_overtime(0, 480, rules) == (480, 0)
    # One minute past the threshold mid-shift: split.
    assert split_overtime(420, 120, rules) == (60, 60)
    # Already past the threshold before this shift started: all overtime.
    assert split_overtime(480, 60, rules) == (0, 60)


def test_weekly_overtime_splits_past_the_period_threshold(rules: Rules) -> None:
    assert weekly_overtime(2400, rules) == (2400, 0)
    assert weekly_overtime(2500, rules) == (2400, 100)


def test_pay_cents_rounds_half_up_and_combines_regular_overtime_and_night(rules: Rules) -> None:
    # 10 minutes at $9.99/hour is exactly $1.665, which rounds up to $1.67.
    assert pay_cents(10, 0, 0, Decimal("9.99"), rules) == Decimal("1.67")
    total = pay_cents(1020, 120, 420, Decimal("22.5000"), rules)
    assert total == Decimal("465.75")


def test_transition_allows_forward_moves_and_rejects_others() -> None:
    assert transition(TimesheetStatus.OPEN, TimesheetStatus.SUBMITTED) == TimesheetStatus.SUBMITTED
    with pytest.raises(InvalidTransition):
        transition(TimesheetStatus.APPROVED, TimesheetStatus.OPEN)
    with pytest.raises(InvalidTransition):
        transition(TimesheetStatus.OPEN, TimesheetStatus.APPROVED)
