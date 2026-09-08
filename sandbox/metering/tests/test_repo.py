"""repo.py: the append-only ledger's latest-non-superseded selection, and
bill lookup for idempotency."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from sandbox.metering.db import Bill, Reading
from sandbox.metering.repo import BillRepo, MeterRepo, ReadingRepo
from sandbox.metering.tests.conftest import EPOCH, METER_SERIAL


def test_reading_repo_latest_excludes_superseded_rows(
    db: Session, seeded: dict[str, object]
) -> None:
    meter = MeterRepo(db).by_serial(METER_SERIAL)
    assert meter is not None
    readings = ReadingRepo(db)

    original = readings.add(
        Reading(meter_id=meter.id, taken_at=EPOCH, value_kwh=Decimal("10.000"), source="actual")
    )
    correction = Reading(
        meter_id=meter.id, taken_at=EPOCH, value_kwh=Decimal("12.000"), source="actual"
    )
    readings.supersede(original.id, correction)

    latest = readings.latest(meter.id)
    assert latest is not None
    assert latest.id == correction.id
    assert latest.value_kwh == Decimal("12.000")


def test_reading_repo_supersede_does_not_delete_the_original(
    db: Session, seeded: dict[str, object]
) -> None:
    meter = MeterRepo(db).by_serial(METER_SERIAL)
    assert meter is not None
    readings = ReadingRepo(db)

    original = readings.add(
        Reading(meter_id=meter.id, taken_at=EPOCH, value_kwh=Decimal("10.000"), source="actual")
    )
    correction = Reading(
        meter_id=meter.id, taken_at=EPOCH, value_kwh=Decimal("12.000"), source="actual"
    )
    readings.supersede(original.id, correction)

    still_there = readings.get(original.id)
    assert still_there is not None
    assert still_there.value_kwh == Decimal("10.000")
    assert correction.supersedes_id == original.id


def test_bill_repo_for_period_finds_the_matching_bill(
    db: Session, seeded: dict[str, object]
) -> None:
    account = seeded["account"]
    start = EPOCH.date()
    end = (EPOCH + timedelta(days=30)).date()
    bills = BillRepo(db)
    assert bills.for_period(account.id, start, end) is None  # type: ignore[attr-defined]

    saved = bills.add(
        Bill(
            account_id=account.id,  # type: ignore[attr-defined]
            period_start=start,
            period_end=end,
            kwh=Decimal("100.000"),
            amount=Decimal("30.00"),
            generated_at=EPOCH,
        )
    )

    found = bills.for_period(account.id, start, end)  # type: ignore[attr-defined]
    assert found is not None
    assert found.id == saved.id
