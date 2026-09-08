"""Deposit and pickup services: business rules layered on the repositories.

Each method opens exactly one unit of work (see sandbox/lockers/db.py) and
either fully succeeds or leaves no trace. Callers pass in `now`; an aware
datetime is rejected here, at the service boundary.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from sandbox.lockers.db import Compartment, Locker, Parcel, ensure_naive_utc, unit_of_work
from sandbox.lockers.domain.fit import (
    Dimensions,
    Size,
    can_redirect,
    expires_at,
    fits,
    late_fee_cents,
)
from sandbox.lockers.repo import CompartmentRepo, ParcelRepo

HOLD_HOURS = 72
GRACE_HOURS = 24
FEE_CENTS_PER_HOUR = 50
FEE_CAP_CENTS = 2000
_CODE_ATTEMPTS = 10
MAX_REDIRECTS = 3

log = logging.getLogger(__name__)


class NoSpace(Exception):
    pass


class InvalidCode(Exception):
    pass


class Expired(Exception):
    pass


class TooManyRedirects(Exception):
    pass


class DifferentSite(Exception):
    pass


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
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def pickup(self, locker_id: int, code: str, now: datetime) -> int:
        """Validate the code, free the compartment, and return the late fee."""
        ensure_naive_utc(now)
        with unit_of_work(self.session_factory) as session:
            parcels = ParcelRepo(session)
            parcel = parcels.by_code(locker_id, code)
            if parcel is None:
                raise InvalidCode(f"no active parcel in locker {locker_id} with that code")
            if now > parcel.expires_at:
                raise Expired(f"parcel {parcel.id} expired at {parcel.expires_at}")
            fee = late_fee_cents(
                parcel.deposited_at, now, GRACE_HOURS, FEE_CENTS_PER_HOUR, FEE_CAP_CENTS
            )
            CompartmentRepo(session).set_occupied(parcel.compartment_id, False)
            parcel.picked_up_at = now
            return fee


def _notify_redirect(parcel: Parcel, target_locker_id: int) -> None:
    log.info(
        "parcel %s redirected to locker %s, new code %s",
        parcel.id,
        target_locker_id,
        parcel.pickup_code,
    )


class RedirectService:
    """Move an undelivered parcel to a different locker at the same site."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def redirect(
        self,
        locker_id: int,
        parcel_id: int,
        code: str,
        target_locker_id: int,
        now: datetime,
    ) -> Parcel:
        """Free the current compartment, allocate one in the target locker,
        and issue a new pickup code there.
        """
        ensure_naive_utc(now)
        with unit_of_work(self.session_factory) as session:
            parcels = ParcelRepo(session)
            compartments = CompartmentRepo(session)

            source_locker = session.get(Locker, locker_id)
            target_locker = session.get(Locker, target_locker_id)
            if source_locker is None or target_locker is None:
                raise InvalidCode(f"no such locker: {locker_id} or {target_locker_id}")
            if target_locker_id == locker_id or source_locker.site != target_locker.site:
                raise DifferentSite("redirect target must be a different locker at the same site")

            parcel = parcels.by_code(locker_id, code)
            if parcel is None or parcel.id != parcel_id:
                raise InvalidCode(f"no active parcel {parcel_id} in locker {locker_id}")
            if not can_redirect(now, parcel.expires_at):
                raise Expired(f"parcel {parcel.id} can no longer be redirected")
            if parcel.redirect_count >= MAX_REDIRECTS:
                raise TooManyRedirects(f"parcel {parcel.id} has reached its redirect limit")

            old_compartment = session.get(Compartment, parcel.compartment_id)
            if old_compartment is None:
                raise InvalidCode(f"parcel {parcel.id} has no compartment")
            size = Size(old_compartment.size)

            compartments.set_occupied(old_compartment.id, False)

            new_compartment = compartments.free_by_size(target_locker_id, size)
            if new_compartment is None:
                raise NoSpace(
                    f"no compartment of size {size.value} free in locker {target_locker_id}"
                )

            new_code = _generate_code(parcels, locker_id)

            compartments.set_occupied(new_compartment.id, True)
            parcel.compartment_id = new_compartment.id
            parcel.pickup_code = new_code
            parcel.deposited_at = now
            parcel.expires_at = expires_at(now, HOLD_HOURS)
            parcel.redirect_count += 1

            _notify_redirect(parcel, target_locker_id)
            return parcel
