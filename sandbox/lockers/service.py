"""Deposit and pickup services: business rules layered on the repositories.

Each method opens exactly one unit of work (see sandbox/lockers/db.py) and
either fully succeeds or leaves no trace. Callers pass in `now`; an aware
datetime is rejected here, at the service boundary.
"""

from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from sandbox.lockers.db import Compartment, Parcel, ensure_naive_utc, unit_of_work
from sandbox.lockers.domain.fit import Dimensions, Size, expires_at, fits, late_fee_cents
from sandbox.lockers.lockout import LockoutTracker
from sandbox.lockers.repo import CompartmentRepo, ParcelRepo

HOLD_HOURS = 72
GRACE_HOURS = 24
FEE_CENTS_PER_HOUR = 50
FEE_CAP_CENTS = 2000
_CODE_ATTEMPTS = 10


class NoSpace(Exception):
    pass


class InvalidCode(Exception):
    pass


class Expired(Exception):
    pass


class LockedOut(Exception):
    def __init__(self, locker_id: int, retry_after_seconds: int) -> None:
        super().__init__(f"locker {locker_id} is locked out for {retry_after_seconds}s")
        self.locker_id = locker_id
        self.retry_after_seconds = retry_after_seconds


def _pick_compartment(
    compartments: CompartmentRepo, locker_id: int, dimensions: Dimensions
) -> Compartment | None:
    """The smallest free compartment the parcel fits in, trying sizes in order."""
    for size in Size:
        if not fits(dimensions, size):
            continue
        compartment = compartments.free_by_size(locker_id, size)
        if compartment is not None:
            return compartment
    return None


def _generate_code(parcels: ParcelRepo, locker_id: int) -> str:
    """A 6-digit code unique among this locker's active parcels."""
    for _ in range(_CODE_ATTEMPTS):
        code = f"{secrets.randbelow(1_000_000):06d}"
        if parcels.by_code(locker_id, code) is None:
            return code
    raise RuntimeError("could not generate a unique pickup code")


class DepositService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def deposit(
        self, locker_id: int, dimensions: Dimensions, recipient_email: str, now: datetime
    ) -> Parcel:
        ensure_naive_utc(now)
        with unit_of_work(self.session_factory) as session:
            compartments = CompartmentRepo(session)
            parcels = ParcelRepo(session)
            compartment = _pick_compartment(compartments, locker_id, dimensions)
            if compartment is None:
                raise NoSpace(f"no compartment in locker {locker_id} fits and is free")
            code = _generate_code(parcels, locker_id)
            compartments.set_occupied(compartment.id, True)
            parcel = Parcel(
                compartment_id=compartment.id,
                recipient_email=recipient_email,
                pickup_code=code,
                deposited_at=now,
                expires_at=expires_at(now, HOLD_HOURS),
            )
            return parcels.add(parcel)


class PickupService:
    def __init__(self, session_factory: sessionmaker[Session], lockout: LockoutTracker) -> None:
        self.session_factory = session_factory
        self._lockout = lockout

    def pickup(self, locker_id: int, code: str, now: datetime) -> int:
        """Validate the code, free the compartment, and return the late fee.

        A wrong code counts against this locker's failed-attempt tracker.
        Once the tracker locks the locker out, every pickup fails fast with
        LockedOut until the lockout clears or a courier clears it early. A
        correct code resets the tracker for this locker.
        """
        ensure_naive_utc(now)
        lockout_status = self._lockout.status(locker_id)
        if lockout_status is not None:
            raise LockedOut(locker_id, lockout_status.retry_after_seconds)

        newly_locked = False
        with unit_of_work(self.session_factory) as session:
            parcels = ParcelRepo(session)
            parcel = parcels.by_code(locker_id, code)
            if parcel is None:
                newly_locked = self._lockout.record_failure(locker_id)
            elif now > parcel.expires_at:
                raise Expired(f"parcel {parcel.id} expired at {parcel.expires_at}")
            else:
                fee = late_fee_cents(
                    parcel.deposited_at, now, GRACE_HOURS, FEE_CENTS_PER_HOUR, FEE_CAP_CENTS
                )
                CompartmentRepo(session).set_occupied(parcel.compartment_id, False)
                parcel.picked_up_at = now
                self._lockout.clear(locker_id)
                return fee

        # The unit of work above touched nothing for a wrong code and is
        # already closed; alert ops outside the transaction so a slow or
        # flaky alert channel never holds a database connection open.
        if newly_locked:
            self._lockout.alert(locker_id)
        raise InvalidCode(f"no active parcel in locker {locker_id} with that code")
