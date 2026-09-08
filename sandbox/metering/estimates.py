"""Batch job: append an estimated reading for meters that have gone
quiet. Not part of the API; run from a scheduler or a script. An estimate
is a reading like any other, source="estimate", appended to the same
ledger, so billing needs no special case for a quiet meter."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from sandbox.metering.db import Reading, coerce_utc, ensure_aware_utc, session_scope
from sandbox.metering.domain.tariff import Tariff, consumption
from sandbox.metering.repo import MeterRepo, ReadingRepo

STALE_AFTER_DAYS = 35
_KWH_PLACES = Decimal("0.001")


def estimate_missing(
    session_factory: sessionmaker[Session], as_of: datetime, tariff: Tariff
) -> int:
    """Append one estimated reading for every meter with no reading in
    the last `STALE_AFTER_DAYS` days, extrapolated from its last two
    actual readings. A meter with fewer than two actual readings is
    skipped. Each meter is visited once per call, so this never appends
    more than one estimate per meter per run. `tariff` is accepted for
    symmetry with `BillingService.generate` but unused here. Returns the
    number of estimates appended."""
    ensure_aware_utc(as_of)
    cutoff = as_of - timedelta(days=STALE_AFTER_DAYS)
    appended = 0

    with session_scope(session_factory) as session:
        meters = MeterRepo(session)
        readings = ReadingRepo(session)
        for meter in meters.all():
            latest = readings.latest(meter.id)
            if latest is not None and coerce_utc(latest.taken_at) >= cutoff:
                continue

            newer, older = _last_two(readings, meter.id)
            if newer is None or older is None:
                continue

            newer_at = coerce_utc(newer.taken_at)
            older_at = coerce_utc(older.taken_at)
            elapsed_days = (newer_at - older_at).total_seconds() / 86400
            if elapsed_days <= 0:
                continue

            delta = consumption(older.value_kwh, newer.value_kwh, meter.max_reading)
            rate_per_day = delta / Decimal(str(elapsed_days))
            since_newer_days = (as_of - newer_at).total_seconds() / 86400
            estimated_value = newer.value_kwh + rate_per_day * Decimal(str(since_newer_days))

            # ponytail: extrapolation ignores rollover past max_reading and
            # clamps instead of wrapping. Fine for a 35-day quiet window on
            # a normal domestic meter; switch to rollover-aware addition if
            # a quiet meter can plausibly wrap within STALE_AFTER_DAYS.
            if estimated_value > meter.max_reading:
                estimated_value = meter.max_reading
            estimated_value = estimated_value.quantize(_KWH_PLACES)

            readings.add(
                Reading(
                    meter_id=meter.id,
                    taken_at=as_of,
                    value_kwh=estimated_value,
                    source="estimate",
                )
            )
            appended += 1

    return appended


def _last_two(readings: ReadingRepo, meter_id: int) -> tuple[Reading | None, Reading | None]:
    """(newer, older) of the meter's last two actual readings, or
    (None, None) if there are fewer than two."""
    rows = readings.last_two_actual(meter_id)
    if len(rows) < 2:
        return None, None
    return rows[0], rows[1]
