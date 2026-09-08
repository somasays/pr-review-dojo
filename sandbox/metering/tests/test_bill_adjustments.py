"""Bill adjustments issued after a reading correction."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from sandbox.metering.service import BillingService, ReadingService
from sandbox.metering.tests.conftest import (
    CUSTOMER_EMAIL,
    EPOCH,
    METER_SERIAL,
    READER_EMAIL,
    READER_KEY,
    TARIFF,
)


def test_correction_issues_an_adjustment_for_the_affected_bill(
    db: Session, seeded: dict[str, object]
) -> None:
    reading_service = ReadingService(db)
    billing = BillingService(db)
    start = EPOCH
    end = EPOCH + timedelta(days=30)
    reading_service.submit(READER_EMAIL, METER_SERIAL, start, Decimal("0.000"), "actual")
    closing = reading_service.submit(
        READER_EMAIL, METER_SERIAL, start + timedelta(days=20), Decimal("100.000"), "actual"
    )
    original = billing.generate(CUSTOMER_EMAIL, start.date(), end.date(), TARIFF)

    corrected = reading_service.correct(READER_EMAIL, closing.id, Decimal("150.000"))
    adjustments = billing.apply_correction(READER_EMAIL, corrected.id, TARIFF)

    assert len(adjustments) == 1
    assert adjustments[0].adjusts_bill_id == original.id
    assert adjustments[0].kwh == Decimal("50.000")
    assert adjustments[0].amount != Decimal("0.00")


def test_reader_can_trigger_the_adjustment_endpoint(
    client: TestClient, seeded: dict[str, object]
) -> None:
    start = EPOCH
    end = EPOCH + timedelta(days=30)
    for at, value in [(start, "0.000"), (start + timedelta(days=20), "40.000")]:
        client.post(
            "/readings",
            headers={"X-Metering-Key": READER_KEY},
            json={
                "meter_serial": METER_SERIAL,
                "taken_at": at.isoformat(),
                "value_kwh": value,
                "source": "actual",
            },
        )
    account_id = seeded["account"].id  # type: ignore[attr-defined]
    client.post(
        f"/accounts/{account_id}/bills",
        headers={"X-Metering-Key": READER_KEY},
        json={"period_start": start.date().isoformat(), "period_end": end.date().isoformat()},
    )

    latest = client.get(
        f"/meters/{METER_SERIAL}/latest", headers={"X-Metering-Key": READER_KEY}
    ).json()
    correction = client.post(
        f"/readings/{latest['id']}/corrections",
        headers={"X-Metering-Key": READER_KEY},
        json={"value_kwh": "60.000"},
    ).json()

    resp = client.post(
        f"/readings/{correction['id']}/adjustments", headers={"X-Metering-Key": READER_KEY}
    )
    assert resp.status_code == 200
    assert resp.json()[0]["adjusts_bill_id"] is not None
