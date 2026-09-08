"""Tests for sandbox/lockers/sweeper.py."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from sandbox.lockers.db import Compartment, Locker, Parcel
from sandbox.lockers.repo import ParcelRepo
from sandbox.lockers.sweeper import sweep

NOW = datetime(2026, 9, 8, 9, 0)


def _seed_parcel(db: Session, locker: Locker, size: str, expires_in: timedelta) -> Parcel:
    compartment = db.query(Compartment).filter_by(locker_id=locker.id, size=size).one()
    compartment.occupied = True
    parcel = Parcel(
        compartment_id=compartment.id,
        recipient_email="ada@example.com",
        pickup_code="123456",
        deposited_at=NOW - timedelta(hours=48),
        expires_at=NOW + expires_in,
    )
    db.add(parcel)
    db.commit()
    return parcel


def test_sweep_notifies_once_and_ignores_parcels_outside_the_window(
    session_factory: sessionmaker[Session], locker: Locker, db: Session
) -> None:
    _seed_parcel(db, locker, "S", timedelta(hours=3))  # within the 6h notice window
    _seed_parcel(db, locker, "M", timedelta(hours=12))  # outside it
    notified: list[str] = []

    result = sweep(NOW, lambda parcel: notified.append(parcel.recipient_email), session_factory)
    assert result == {"notified": 1, "deleted": 0}

    result = sweep(NOW, lambda parcel: notified.append(parcel.recipient_email), session_factory)
    assert result["notified"] == 0  # already notified, not repeated
    assert notified == ["ada@example.com"]


def test_sweep_deletes_expired_parcel_and_frees_its_compartment(
    session_factory: sessionmaker[Session], locker: Locker, db: Session
) -> None:
    parcel = _seed_parcel(db, locker, "S", timedelta(hours=-1))
    result = sweep(NOW, lambda parcel: None, session_factory)
    assert result["deleted"] == 1

    with session_factory() as session:
        assert ParcelRepo(session).get(parcel.id) is None
        compartment = session.get(Compartment, parcel.compartment_id)
        assert compartment is not None
        assert compartment.occupied is False
