"""service.py: reading submission and correction, bill generation."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from sandbox.metering.repo import BillRepo, MeterRepo, ReadingRepo
from sandbox.metering.service import (
    BillingService,
    InvalidReading,
    NotAllowed,
    ReadingService,
)
from sandbox.metering.tests.conftest import (
    CUSTOMER_EMAIL,
    EPOCH,
    MAX_READING,
    METER_SERIAL,
    READER_EMAIL,
    TARIFF,
)


def test_reading_service_submit_appends_a_reading(db: Session, seeded: dict[str, object]) -> None:
    reading = ReadingService(db).submit(
        READER_EMAIL, METER_SERIAL, EPOCH, Decimal("10.000"), "actual"
    )
    assert reading.id is not None
    assert reading.value_kwh == Decimal("10.000")


def test_reading_service_submit_rejects_a_reading_above_max_reading(
    db: Session, seeded: dict[str, object]
) -> None:
    service = ReadingService(db)
    service.submit(READER_EMAIL, METER_SERIAL, EPOCH, Decimal("10.000"), "actual")
    with pytest.raises(InvalidReading):
        service.submit(
            READER_EMAIL,
            METER_SERIAL,
            EPOCH + timedelta(days=1),
            MAX_READING + 1,
            "actual",
        )


def test_reading_service_correct_appends_a_superseding_row(
    db: Session, seeded: dict[str, object]
) -> None:
    service = ReadingService(db)
    original = service.submit(READER_EMAIL, METER_SERIAL, EPOCH, Decimal("10.000"), "actual")

    corrected = service.correct(READER_EMAIL, original.id, Decimal("15.000"))

    assert corrected.supersedes_id == original.id
    assert corrected.value_kwh == Decimal("15.000")
    meter = MeterRepo(db).by_serial(METER_SERIAL)
    assert meter is not None
    latest = ReadingRepo(db).latest(meter.id)
    assert latest is not None
    assert latest.id == corrected.id


def test_billing_service_generate_computes_consumption_and_charge(
    db: Session, seeded: dict[str, object]
) -> None:
    service = ReadingService(db)
    start = EPOCH
    end = EPOCH + timedelta(days=30)
    service.submit(READER_EMAIL, METER_SERIAL, start, Decimal("0.000"), "actual")
    service.submit(READER_EMAIL, METER_SERIAL, end, Decimal("150.000"), "actual")

    bill = BillingService(db).generate(CUSTOMER_EMAIL, start.date(), end.date(), TARIFF)

    assert bill.kwh == Decimal("150.000")
    # 100 kWh @ 0.30 + 50 kWh @ 0.20 + 30 days @ 0.20 standing charge.
    assert bill.amount == Decimal("30.00") + Decimal("10.00") + Decimal("6.00")


def test_billing_service_generate_is_idempotent_per_period(
    db: Session, seeded: dict[str, object]
) -> None:
    service = ReadingService(db)
    start = EPOCH
    end = EPOCH + timedelta(days=30)
    service.submit(READER_EMAIL, METER_SERIAL, start, Decimal("0.000"), "actual")
    service.submit(READER_EMAIL, METER_SERIAL, end, Decimal("50.000"), "actual")

    billing = BillingService(db)
    first = billing.generate(CUSTOMER_EMAIL, start.date(), end.date(), TARIFF)
    second = billing.generate(CUSTOMER_EMAIL, start.date(), end.date(), TARIFF)

    assert first.id == second.id
    account = seeded["account"]
    assert len(BillRepo(db).for_account(account.id)) == 1  # type: ignore[union-attr]


def test_reading_service_correct_rejects_an_already_superseded_reading(
    db: Session, seeded: dict[str, object]
) -> None:
    service = ReadingService(db)
    original = service.submit(READER_EMAIL, METER_SERIAL, EPOCH, Decimal("10.000"), "actual")
    service.correct(READER_EMAIL, original.id, Decimal("15.000"))

    with pytest.raises(NotAllowed):
        service.correct(READER_EMAIL, original.id, Decimal("20.000"))
