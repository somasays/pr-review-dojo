"""Tests for sandbox/rooms/domain/slots.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from sandbox.rooms.domain.slots import MIN_CHARGE_CENTS, Slot, can_amend, price_cents


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 8, hour, minute, tzinfo=UTC)


def test_slot_normalizes_non_utc_timezone_to_utc() -> None:
    minus_five = timezone(timedelta(hours=-5))
    slot = Slot(
        datetime(2026, 9, 8, 5, 0, tzinfo=minus_five), datetime(2026, 9, 8, 6, 0, tzinfo=minus_five)
    )
    assert slot.start == _dt(10, 0)
    assert slot.end == _dt(11, 0)


def test_slot_boundary_validation() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Slot(datetime(2026, 9, 8, 9, 0), _dt(10, 0))
    with pytest.raises(ValueError, match="half hour"):
        Slot(_dt(9, 15), _dt(10, 0))
    with pytest.raises(ValueError, match="not after"):
        Slot(_dt(10, 0), _dt(9, 0))
    with pytest.raises(ValueError, match="exceeds"):
        Slot(_dt(0, 0), _dt(8, 30))
    # Exactly the maximum duration is allowed.
    assert Slot(_dt(0, 0), _dt(8, 0)).duration_minutes() == 480


def test_overlaps_true_for_partial_overlap() -> None:
    a = Slot(_dt(9, 0), _dt(10, 0))
    b = Slot(_dt(9, 30), _dt(10, 30))
    assert a.overlaps(b)
    assert b.overlaps(a)


def test_overlaps_false_for_adjacent_slots() -> None:
    a = Slot(_dt(9, 0), _dt(10, 0))
    b = Slot(_dt(10, 0), _dt(11, 0))
    assert not a.overlaps(b)
    assert not b.overlaps(a)


def test_split_into_half_hours() -> None:
    parts = Slot(_dt(9, 0), _dt(10, 30)).split_into_half_hours()
    assert [p.start.hour * 60 + p.start.minute for p in parts] == [540, 570, 600]
    assert all(p.duration_minutes() == 30 for p in parts)


def test_price_cents_non_member() -> None:
    slot = Slot(_dt(9, 0), _dt(11, 0))
    assert price_cents(slot, rate_cents_per_hour=2000, member=False) == 4000


def test_price_cents_member_discount_and_minimum_charge() -> None:
    two_hours = Slot(_dt(9, 0), _dt(11, 0))
    assert price_cents(two_hours, rate_cents_per_hour=2000, member=True) == 3400

    half_hour = Slot(_dt(9, 0), _dt(9, 30))
    assert price_cents(half_hour, rate_cents_per_hour=100, member=True) == MIN_CHARGE_CENTS


def test_price_cents_rejects_negative_rate() -> None:
    slot = Slot(_dt(9, 0), _dt(10, 0))
    with pytest.raises(ValueError, match="negative"):
        price_cents(slot, rate_cents_per_hour=-1, member=False)


def test_can_amend_true_well_before_the_start() -> None:
    now = _dt(9, 0)
    assert can_amend(now, now + timedelta(hours=2))


def test_can_amend_false_once_the_booking_has_started() -> None:
    now = _dt(9, 0)
    assert not can_amend(now, now - timedelta(minutes=1))
