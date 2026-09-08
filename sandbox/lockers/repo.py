"""Repositories: the only place that builds queries.

Convention (see sandbox/lockers/README.md): repositories never commit. The
service layer owns the transaction through db.unit_of_work().
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from sandbox.lockers.db import Compartment, Parcel
from sandbox.lockers.domain.fit import Size


class CompartmentRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def free_by_size(self, locker_id: int, size: Size) -> Compartment | None:
        """The first free compartment of exactly this size, if any."""
        stmt = (
            select(Compartment)
            .where(
                Compartment.locker_id == locker_id,
                Compartment.size == size.value,
                Compartment.occupied.is_(False),
            )
            .order_by(Compartment.id)
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def set_occupied(self, compartment_id: int, occupied: bool) -> None:
        compartment = self.session.get(Compartment, compartment_id)
        if compartment is not None:
            compartment.occupied = occupied

    def list_by_locker(self, locker_id: int) -> Sequence[Compartment]:
        stmt = select(Compartment).where(Compartment.locker_id == locker_id)
        return self.session.scalars(stmt).all()


class ParcelRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, parcel: Parcel) -> Parcel:
        self.session.add(parcel)
        self.session.flush()
        return parcel

    def get(self, parcel_id: int) -> Parcel | None:
        return self.session.get(Parcel, parcel_id)

    def by_code(self, locker_id: int, code: str) -> Parcel | None:
        """The active (not yet picked up) parcel in this locker with this code."""
        stmt = (
            select(Parcel)
            .join(Compartment, Parcel.compartment_id == Compartment.id)
            .where(
                Compartment.locker_id == locker_id,
                Parcel.pickup_code == code,
                Parcel.picked_up_at.is_(None),
            )
        )
        return self.session.scalars(stmt).first()

    def expired(self, now: datetime) -> Sequence[Parcel]:
        """Active parcels whose expiry is at or before `now`."""
        stmt = select(Parcel).where(Parcel.picked_up_at.is_(None), Parcel.expires_at <= now)
        return self.session.scalars(stmt).all()

    def delete(self, parcel: Parcel) -> None:
        self.session.delete(parcel)

    def mark_notified(self, parcel_id: int, now: datetime) -> None:
        parcel = self.session.get(Parcel, parcel_id)
        if parcel is not None:
            parcel.notified_at = now
