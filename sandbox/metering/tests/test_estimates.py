"""estimates.py: estimate_missing, the quiet-meter batch job."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from sandbox.metering.db import Reading
from sandbox.metering.estimates import estimate_missing
from sandbox.metering.repo import MeterRepo, ReadingRepo
from sandbox.metering.tests.conftest import EPOCH, METER_SERIAL, TARIFF


def test_estimate_missing_extrapolates_once_per_stale_meter(
    db: Session, session_factory: sessionmaker[Session], seeded: dict[str, object]
) -> None:
    meter = MeterRepo(db).by_serial(METER_SERIAL)
    assert meter is not None
    readings = ReadingRepo(db)
    readings.add(
        Reading(meter_id=meter.id, taken_at=EPOCH, value_kwh=Decimal("0.000"), source="actual")
    )
    readings.add(
        Reading(
            meter_id=meter.id,
            taken_at=EPOCH + timedelta(days=10),
            value_kwh=Decimal("100.000"),
            source="actual",
        )
    )
    db.commit()

    as_of = EPOCH + timedelta(days=50)  # more than STALE_AFTER_DAYS past the last actual
    appended = estimate_missing(session_factory, as_of, TARIFF)
    assert appended == 1

    latest = readings.latest(meter.id)
    assert latest is not None
    assert latest.source == "estimate"
    # 10 kWh/day for 40 more days past the newer actual reading.
    assert latest.value_kwh == Decimal("500.000")

    # Running it again for the same as_of does not append a second
    # estimate for the same meter: the estimate just appended is now the
    # latest reading, and it is not stale.
    again = estimate_missing(session_factory, as_of, TARIFF)
    assert again == 0


def test_estimate_missing_skips_a_meter_with_a_recent_reading(
    db: Session, session_factory: sessionmaker[Session], seeded: dict[str, object]
) -> None:
    meter = MeterRepo(db).by_serial(METER_SERIAL)
    assert meter is not None
    readings = ReadingRepo(db)
    readings.add(
        Reading(meter_id=meter.id, taken_at=EPOCH, value_kwh=Decimal("5.000"), source="actual")
    )
    db.commit()

    as_of = EPOCH + timedelta(days=1)  # well within STALE_AFTER_DAYS
    appended = estimate_missing(session_factory, as_of, TARIFF)
    assert appended == 0
    assert readings.latest(meter.id).source == "actual"  # type: ignore[union-attr]
