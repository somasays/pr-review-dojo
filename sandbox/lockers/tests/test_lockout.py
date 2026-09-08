"""Tests for sandbox/lockers/lockout.py and the pickup lockout it protects."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from sandbox.lockers.db import Locker
from sandbox.lockers.domain.fit import Dimensions
from sandbox.lockers.lockout import LockoutPolicy, LockoutTracker
from sandbox.lockers.service import DepositService, InvalidCode, LockedOut, PickupService

SMALL = Dimensions(10, 10, 10)
NOW = datetime(2026, 9, 8, 9, 0)


def _services(
    session_factory: sessionmaker[Session], tracker: LockoutTracker
) -> tuple[DepositService, PickupService]:
    return DepositService(session_factory), PickupService(session_factory, tracker)


def test_locker_locks_out_after_max_wrong_codes(
    session_factory: sessionmaker[Session], locker: Locker
) -> None:
    policy = LockoutPolicy(max_attempts=3, window_seconds=60, lockout_minutes=1, sweep_seconds=3600)
    _, pickup_service = _services(session_factory, LockoutTracker(policy))

    for _ in range(3):
        with pytest.raises(InvalidCode):
            pickup_service.pickup(locker.id, "000000", NOW)

    with pytest.raises(LockedOut) as info:
        pickup_service.pickup(locker.id, "000000", NOW)
    assert info.value.retry_after_seconds > 0


def test_correct_pickup_clears_the_counter(
    session_factory: sessionmaker[Session], locker: Locker
) -> None:
    policy = LockoutPolicy(max_attempts=3, window_seconds=60, lockout_minutes=1, sweep_seconds=3600)
    tracker = LockoutTracker(policy)
    deposit_service, pickup_service = _services(session_factory, tracker)
    parcel = deposit_service.deposit(locker.id, SMALL, "ada@example.com", NOW)

    for _ in range(2):
        with pytest.raises(InvalidCode):
            pickup_service.pickup(locker.id, "000000", NOW)

    pickup_service.pickup(locker.id, parcel.pickup_code, NOW)

    # Two more wrong codes should not lock the locker out: the earlier
    # attempts were cleared by the successful pickup above.
    for _ in range(2):
        with pytest.raises(InvalidCode):
            pickup_service.pickup(locker.id, "111111", NOW)
    assert tracker.status(locker.id) is None


def test_sweep_expires_lockouts_and_prunes_idle_counters() -> None:
    # An injected clock makes this deterministic: no sleep, no real thread,
    # no waiting on wall-clock time to find out whether the sweep worked.
    now = [0.0]
    policy = LockoutPolicy(max_attempts=1, window_seconds=10, lockout_minutes=1, sweep_seconds=3600)
    tracker = LockoutTracker(policy, clock=lambda: now[0])

    tracker.record_failure(1)
    assert tracker.status(1) is not None

    now[0] += 61  # past the one minute lockout
    tracker._sweep()
    assert tracker.status(1) is None
    assert 1 not in tracker._attempts  # idle counter pruned, not just the lockout cleared
