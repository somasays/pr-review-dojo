"""Hidden tests for exercise 49: bill adjustments after a reading
correction.

Each test below fails against ex/49-bill-adjustments and passes against
solutions/49.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from sandbox.metering.db import Bill, Reading
from sandbox.metering.domain.tariff import adjustment_amount
from sandbox.metering.repo import BillRepo
from sandbox.metering.service import BillingService, ReadingService
from solutions_tests.conftest import (
    CUSTOMER_EMAIL,
    CUSTOMER_KEY,
    EPOCH,
    METER_SERIAL,
    READER_EMAIL,
    READER_KEY,
    TARIFF,
)

# --- Domain arithmetic: adjustment_amount subtracted in the wrong order,
# --- so a correction that raises the bill produced a negative adjustment
# --- and vice versa (LG-01). -------------------------------------------


def test_adjustment_amount_is_positive_when_the_correction_raises_the_bill() -> None:
    assert adjustment_amount(Decimal("36.00"), Decimal("46.00")) == Decimal("10.00")
    assert adjustment_amount(Decimal("46.00"), Decimal("36.00")) == Decimal("-10.00")


def _bill_period(
    reading_service: ReadingService, billing: BillingService, start: datetime, end: datetime
) -> tuple[Bill, Reading]:
    reading_service.submit(READER_EMAIL, METER_SERIAL, start, Decimal("0.000"), "actual")
    closing = reading_service.submit(
        READER_EMAIL, METER_SERIAL, start + timedelta(days=20), Decimal("100.000"), "actual"
    )
    original = billing.generate(CUSTOMER_EMAIL, start.date(), end.date(), TARIFF)
    return original, closing


def test_apply_correction_amount_matches_the_recomputed_charge(
    db: Session, seeded: dict[str, object]
) -> None:
    reading_service = ReadingService(db)
    billing = BillingService(db)
    start = EPOCH
    end = EPOCH + timedelta(days=30)
    original, closing = _bill_period(reading_service, billing, start, end)

    corrected = reading_service.correct(READER_EMAIL, closing.id, Decimal("150.000"))
    adjustments = billing.apply_correction(READER_EMAIL, corrected.id, TARIFF)

    assert len(adjustments) == 1
    # 150 kWh: 100 @ 0.30 + 50 @ 0.20 + 6.00 standing = 46.00, versus the
    # original 100 kWh bill of 36.00. The correction raised the bill, so
    # the adjustment must be positive.
    assert adjustments[0].amount == Decimal("10.00")


# --- Service: apply_correction had no idempotency check, so calling it
# --- twice for the same correction issued a second adjustment for the
# --- same original bill (SV-01). ----------------------------------------


def test_apply_correction_is_idempotent_per_correction(
    db: Session, seeded: dict[str, object]
) -> None:
    reading_service = ReadingService(db)
    billing = BillingService(db)
    start = EPOCH
    end = EPOCH + timedelta(days=30)
    original, closing = _bill_period(reading_service, billing, start, end)

    corrected = reading_service.correct(READER_EMAIL, closing.id, Decimal("150.000"))
    first = billing.apply_correction(READER_EMAIL, corrected.id, TARIFF)
    second = billing.apply_correction(READER_EMAIL, corrected.id, TARIFF)

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].id == second[0].id
    account = seeded["account"]
    all_bills = BillRepo(db).for_account(account.id)
    adjustments = [b for b in all_bills if b.adjusts_bill_id == original.id]
    assert len(adjustments) == 1


# --- Repository: affected_by used >= on period_end, an inclusive upper
# --- bound that contradicts the half-open [start, end) convention, so a
# --- correction dated exactly on a bill's period_end wrongly counted as
# --- affecting that bill (LG-07). ----------------------------------------


def test_affected_by_excludes_a_reading_taken_exactly_at_period_end(
    db: Session, seeded: dict[str, object]
) -> None:
    reading_service = ReadingService(db)
    billing = BillingService(db)
    start = EPOCH
    end = EPOCH + timedelta(days=30)
    _bill_period(reading_service, billing, start, end)

    account = seeded["account"]
    bills = BillRepo(db)
    at_end = bills.affected_by(account.id, end)
    just_before_end = bills.affected_by(account.id, end - timedelta(days=1))

    assert at_end == []
    assert len(just_before_end) == 1


# --- API: the new endpoint took any valid key instead of a reader key,
# --- so a customer could trigger recomputation across accounts. ----------


def test_apply_correction_endpoint_requires_a_reader_key(
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
    account_id = seeded["account"].id
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
        f"/readings/{correction['id']}/adjustments", headers={"X-Metering-Key": CUSTOMER_KEY}
    )
    assert resp.status_code == 403


# --- Design: generate() and the new recompute path duplicated the same
# --- "sum consumption across meters for a period" loop instead of
# --- sharing one helper (DS-20). ------------------------------------------


def test_generate_shares_the_consumption_loop_with_apply_correction() -> None:
    source = inspect.getsource(BillingService)
    assert source.count("latest_at_or_before") == 2  # once in _kwh_for_period, used by both


# --- Design: affected_by took a speculative meter_id kwarg that nothing
# --- in the codebase ever passed (DS-17). ---------------------------------


def test_affected_by_has_no_unused_parameter() -> None:
    params = list(inspect.signature(BillRepo.affected_by).parameters)
    assert params == ["self", "account_id", "taken_at"]
