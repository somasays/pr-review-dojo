"""Tests for sandbox/lockers/lockout.py and the pickup lockout it protects."""

from __future__ import annotations

import time
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


def test_background_thread_prunes_expired_lockouts() -> None:
    policy = LockoutPolicy(max_attempts=1, window_seconds=60, lockout_minutes=0, sweep_seconds=0.05)
    tracker = LockoutTracker(policy)
    tracker.start()
    tracker.record_failure(1)

    time.sleep(0.2)  # give the sweep a chance to notice the lockout has expired
    assert tracker.status(1) is None
    tracker.stop()
