"""Sweeper job: notify recipients of parcels expiring soon and hard-delete
parcels that have already expired.

Each notification and each deletion runs in its own unit of work, so a
failure partway through leaves already-processed parcels durably done and
does not roll them back.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from sandbox.lockers.db import Parcel, ensure_naive_utc, get_session_factory, unit_of_work
from sandbox.lockers.repo import CompartmentRepo, ParcelRepo

NOTICE_WINDOW = timedelta(hours=6)

Notifier = Callable[[Parcel], None]


def sweep(
    now: datetime,
    notifier: Notifier,
    session_factory: sessionmaker[Session] | None = None,
) -> dict[str, int]:
    """Notify and delete parcels near or past expiry.

    Parcels expiring within NOTICE_WINDOW get `notifier(parcel)` called
    once, tracked by `notified_at`, so a repeat run skips them. Parcels
    already past `expires_at` are hard-deleted and their compartment is
    freed.
    """
    ensure_naive_utc(now)
    factory = session_factory or get_session_factory()

    with unit_of_work(factory) as session:
        parcels = ParcelRepo(session)
        due_soon_ids = [
            p.id
            for p in parcels.expired(now + NOTICE_WINDOW)
            if p.notified_at is None and p.expires_at > now
        ]
        expired_ids = [p.id for p in parcels.expired(now)]

    notified = 0
    for parcel_id in due_soon_ids:
        with unit_of_work(factory) as session:
            parcels = ParcelRepo(session)
            parcel = parcels.get(parcel_id)
            if parcel is None or parcel.notified_at is not None:
                continue
            notifier(parcel)
            parcels.mark_notified(parcel.id, now)
            notified += 1

    deleted = 0
    for parcel_id in expired_ids:
        with unit_of_work(factory) as session:
            parcels = ParcelRepo(session)
            parcel = parcels.get(parcel_id)
            if parcel is None:
                continue
            CompartmentRepo(session).set_occupied(parcel.compartment_id, False)
            parcels.delete(parcel)
            deleted += 1

    return {"notified": notified, "deleted": deleted}
