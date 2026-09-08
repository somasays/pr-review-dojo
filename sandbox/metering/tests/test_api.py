"""api.py: role-based key auth and the read/write endpoints."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from sandbox.metering.tests.conftest import (
    CUSTOMER_KEY,
    EPOCH,
    METER_SERIAL,
    OTHER_CUSTOMER_KEY,
    READER_KEY,
)


def _headers(key: str) -> dict[str, str]:
    return {"X-Metering-Key": key}


def test_missing_key_is_rejected(client: TestClient) -> None:
    resp = client.get(f"/meters/{METER_SERIAL}/latest")
    assert resp.status_code == 401


def test_reader_can_submit_a_reading(client: TestClient) -> None:
    resp = client.post(
        "/readings",
        headers=_headers(READER_KEY),
        json={
            "meter_serial": METER_SERIAL,
            "taken_at": EPOCH.isoformat(),
            "value_kwh": "12.500",
            "source": "actual",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["value_kwh"] == "12.500"


def test_customer_cannot_submit_a_reading(client: TestClient) -> None:
    resp = client.post(
        "/readings",
        headers=_headers(CUSTOMER_KEY),
        json={
            "meter_serial": METER_SERIAL,
            "taken_at": EPOCH.isoformat(),
            "value_kwh": "1.000",
            "source": "actual",
        },
    )
    assert resp.status_code == 403


def test_reader_can_generate_a_bill_and_customer_can_read_it(
    client: TestClient, seeded: dict[str, object]
) -> None:
    start = EPOCH
    end = EPOCH + timedelta(days=30)
    for at, value in [(start, "0.000"), (end, "40.000")]:
        client.post(
            "/readings",
            headers=_headers(READER_KEY),
            json={
                "meter_serial": METER_SERIAL,
                "taken_at": at.isoformat(),
                "value_kwh": value,
                "source": "actual",
            },
        )

    account_id = seeded["account"].id  # type: ignore[attr-defined]
    gen = client.post(
        f"/accounts/{account_id}/bills",
        headers=_headers(READER_KEY),
        json={"period_start": start.date().isoformat(), "period_end": end.date().isoformat()},
    )
    assert gen.status_code == 201

    own = client.get(f"/accounts/{account_id}/bills", headers=_headers(CUSTOMER_KEY))
    assert own.status_code == 200
    assert len(own.json()) == 1


def test_customer_cannot_read_another_accounts_bills(
    client: TestClient, seeded: dict[str, object]
) -> None:
    account_id = seeded["account"].id  # type: ignore[attr-defined]
    resp = client.get(f"/accounts/{account_id}/bills", headers=_headers(OTHER_CUSTOMER_KEY))
    assert resp.status_code == 403
