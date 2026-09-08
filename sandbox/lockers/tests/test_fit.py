"""Tests for sandbox/lockers/domain/fit.py."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from sandbox.lockers.domain.fit import (
    Dimensions,
    Size,
    can_redirect,
    expires_at,
    fits,
    late_fee_cents,
    smallest_size_for,
)

DEPOSITED = datetime(2026, 9, 8, 9, 0)


def test_dimensions_rejects_non_positive() -> None:
    with pytest.raises(ValueError):
        Dimensions(0, 10, 10)


def test_fits_boundary_is_inclusive_and_not_rotated() -> None:
    assert fits(Dimensions(20, 20, 20), Size.S)
    assert not fits(Dimensions(21, 20, 20), Size.S)
    # 20x20x21 does not fit S even though a rotation would let it, since
    # parcels are never rotated to fit a smaller size.
    assert not fits(Dimensions(20, 20, 21), Size.S)
    assert fits(Dimensions(20, 20, 21), Size.M)


def test_smallest_size_for_picks_the_smallest_fitting_size() -> None:
    assert smallest_size_for(Dimensions(10, 10, 10)) is Size.S
    assert smallest_size_for(Dimensions(30, 30, 30)) is Size.M
    assert smallest_size_for(Dimensions(50, 50, 50)) is Size.L
    assert smallest_size_for(Dimensions(61, 61, 61)) is None


def test_late_fee_within_grace_is_zero() -> None:
    picked_up = DEPOSITED + timedelta(hours=24)
    assert late_fee_cents(DEPOSITED, picked_up, 24, 50, 2000) == 0


def test_late_fee_rounds_up_and_is_capped() -> None:
    # 1 hour and 1 minute past the grace period rounds up to 2 chargeable hours.
    just_over = DEPOSITED + timedelta(hours=25, minutes=1)
    assert late_fee_cents(DEPOSITED, just_over, 24, 50, 2000) == 100

    way_over = DEPOSITED + timedelta(hours=24 + 1000)
    assert late_fee_cents(DEPOSITED, way_over, 24, 50, 2000) == 2000


def test_late_fee_rejects_pickup_before_deposit() -> None:
    with pytest.raises(ValueError):
        late_fee_cents(DEPOSITED, DEPOSITED - timedelta(hours=1), 24, 50, 2000)


def test_expires_at_adds_hold_hours() -> None:
    assert expires_at(DEPOSITED, 72) == DEPOSITED + timedelta(hours=72)


def test_can_redirect_boundary_is_inclusive() -> None:
    deadline = DEPOSITED + timedelta(hours=72)
    assert can_redirect(deadline, deadline)
    assert not can_redirect(deadline + timedelta(seconds=1), deadline)
