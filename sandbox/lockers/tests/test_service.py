"""Tests for sandbox/lockers/service.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from sandbox.lockers.db import Compartment, Locker
from sandbox.lockers.domain.fit import Dimensions
from sandbox.lockers.service import (
    DepositService,
    Expired,
    InvalidCode,
    NoSpace,
    PickupService,
    RedirectService,
    TooManyRedirects,
)

SMALL = Dimensions(10, 10, 10)
NOW = datetime(2026, 9, 8, 9, 0)


def _extra_locker(db: Session, site: str = "Main St") -> Locker:
    """Another locker with one compartment of each size."""
    loc = Locker(site=site, active=True)
    db.add(loc)
    db.flush()
    for size in ("S", "M", "L"):
        db.add(Compartment(locker_id=loc.id, size=size, occupied=False))
    db.commit()
    return loc


@pytest.fixture
def deposit_service(session_factory: sessionmaker[Session]) -> DepositService:
    return DepositService(session_factory)


@pytest.fixture
def pickup_service(session_factory: sessionmaker[Session]) -> PickupService:
    return PickupService(session_factory)


@pytest.fixture
def redirect_service(session_factory: sessionmaker[Session]) -> RedirectService:
    return RedirectService(session_factory)


@pytest.fixture
def other_locker(db: Session) -> Locker:
    return _extra_locker(db)


def test_deposit_picks_smallest_fitting_compartment(
    deposit_service: DepositService, locker: Locker, db: Session
) -> None:
    parcel = deposit_service.deposit(locker.id, SMALL, "ada@example.com", NOW)
    compartment = db.get(Compartment, parcel.compartment_id)
    assert compartment is not None
    assert compartment.size == "S"
    assert compartment.occupied is True
    assert len(parcel.pickup_code) == 6 and parcel.pickup_code.isdigit()


def test_deposit_falls_back_to_next_size_when_smallest_is_full(
    deposit_service: DepositService, locker: Locker, db: Session
) -> None:
    deposit_service.deposit(locker.id, SMALL, "ada@example.com", NOW)
    parcel = deposit_service.deposit(locker.id, SMALL, "bea@example.com", NOW)
    compartment = db.get(Compartment, parcel.compartment_id)
    assert compartment is not None
    assert compartment.size == "M"


def test_deposit_raises_no_space(deposit_service: DepositService, locker: Locker) -> None:
    with pytest.raises(NoSpace):
        deposit_service.deposit(locker.id, Dimensions(100, 100, 100), "ada@example.com", NOW)

    deposit_service.deposit(locker.id, SMALL, "a@example.com", NOW)
    deposit_service.deposit(locker.id, SMALL, "b@example.com", NOW)
    deposit_service.deposit(locker.id, SMALL, "c@example.com", NOW)
    with pytest.raises(NoSpace):
        deposit_service.deposit(locker.id, SMALL, "d@example.com", NOW)


def test_deposit_rejects_aware_datetime(deposit_service: DepositService, locker: Locker) -> None:
    with pytest.raises(ValueError):
        deposit_service.deposit(locker.id, SMALL, "ada@example.com", NOW.replace(tzinfo=UTC))


def test_pickup_with_correct_code_frees_compartment_with_no_fee(
    deposit_service: DepositService, pickup_service: PickupService, locker: Locker, db: Session
) -> None:
    parcel = deposit_service.deposit(locker.id, SMALL, "ada@example.com", NOW)
    fee = pickup_service.pickup(locker.id, parcel.pickup_code, NOW + timedelta(hours=1))
    assert fee == 0
    compartment = db.get(Compartment, parcel.compartment_id)
    assert compartment is not None
    assert compartment.occupied is False


def test_pickup_after_grace_period_charges_a_late_fee(
    deposit_service: DepositService, pickup_service: PickupService, locker: Locker
) -> None:
    parcel = deposit_service.deposit(locker.id, SMALL, "ada@example.com", NOW)
    late_time = NOW + timedelta(hours=30)  # past the 24h grace, before the 72h expiry
    fee = pickup_service.pickup(locker.id, parcel.pickup_code, late_time)
    assert fee > 0


def test_pickup_with_wrong_code_raises_invalid_code(
    deposit_service: DepositService, pickup_service: PickupService, locker: Locker
) -> None:
    parcel = deposit_service.deposit(locker.id, SMALL, "ada@example.com", NOW)
    wrong_code = "000000" if parcel.pickup_code != "000000" else "111111"
    with pytest.raises(InvalidCode):
        pickup_service.pickup(locker.id, wrong_code, NOW)


def test_pickup_after_expiry_raises_expired(
    deposit_service: DepositService, pickup_service: PickupService, locker: Locker
) -> None:
    parcel = deposit_service.deposit(locker.id, SMALL, "ada@example.com", NOW)
    past_expiry = parcel.expires_at + timedelta(hours=1)
    with pytest.raises(Expired):
        pickup_service.pickup(locker.id, parcel.pickup_code, past_expiry)


def test_redirect_moves_parcel_and_issues_a_new_code(
    deposit_service: DepositService,
    redirect_service: RedirectService,
    locker: Locker,
    other_locker: Locker,
    db: Session,
) -> None:
    parcel = deposit_service.deposit(locker.id, SMALL, "ada@example.com", NOW)
    old_compartment_id, original_code = parcel.compartment_id, parcel.pickup_code

    redirected = redirect_service.redirect(
        locker.id, parcel.id, original_code, other_locker.id, NOW + timedelta(hours=1)
    )

    assert redirected.compartment_id != old_compartment_id
    assert redirected.pickup_code != original_code
    old_compartment = db.get(Compartment, old_compartment_id)
    assert old_compartment is not None
    assert old_compartment.occupied is False
    new_compartment = db.get(Compartment, redirected.compartment_id)
    assert new_compartment is not None
    assert new_compartment.occupied is True
    assert new_compartment.locker_id == other_locker.id


def test_redirect_limit_eventually_blocks_further_redirects(
    deposit_service: DepositService,
    redirect_service: RedirectService,
    locker: Locker,
    db: Session,
) -> None:
    """A parcel cannot hop between lockers forever."""
    targets = [_extra_locker(db) for _ in range(5)]
    parcel = deposit_service.deposit(locker.id, SMALL, "ada@example.com", NOW)

    current = locker
    hit_limit = False
    for target in targets:
        try:
            parcel = redirect_service.redirect(
                current.id, parcel.id, parcel.pickup_code, target.id, NOW
            )
            current = target
        except TooManyRedirects:
            hit_limit = True
            break

    assert hit_limit
