"""Tests for sandbox/lockers/api.py."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from sandbox.lockers.db import Compartment, Locker
from sandbox.lockers.tests.conftest import COURIER_KEY

AUTH = {"X-Courier-Key": COURIER_KEY}
DEPOSIT_BODY = {
    "width_cm": 10,
    "height_cm": 10,
    "depth_cm": 10,
    "recipient_email": "ada@example.com",
}


def test_courier_only_endpoints_require_key(client: TestClient, locker: Locker) -> None:
    assert client.post(f"/lockers/{locker.id}/parcels", json=DEPOSIT_BODY).status_code == 401
    assert client.get(f"/lockers/{locker.id}/compartments").status_code == 401


def test_deposit_and_pickup_round_trip(client: TestClient, locker: Locker) -> None:
    deposited = client.post(f"/lockers/{locker.id}/parcels", json=DEPOSIT_BODY, headers=AUTH)
    assert deposited.status_code == 201
    code = deposited.json()["pickup_code"]

    picked_up = client.post(f"/lockers/{locker.id}/pickup", json={"code": code})
    assert picked_up.status_code == 200
    assert picked_up.json()["late_fee_cents"] == 0


def test_pickup_with_wrong_code_is_not_found(client: TestClient, locker: Locker) -> None:
    deposited = client.post(f"/lockers/{locker.id}/parcels", json=DEPOSIT_BODY, headers=AUTH)
    real_code = deposited.json()["pickup_code"]
    wrong_code = "000000" if real_code != "000000" else "111111"

    resp = client.post(f"/lockers/{locker.id}/pickup", json={"code": wrong_code})
    assert resp.status_code == 404


def test_compartments_summary_reports_occupancy(client: TestClient, locker: Locker) -> None:
    client.post(f"/lockers/{locker.id}/parcels", json=DEPOSIT_BODY, headers=AUTH)
    resp = client.get(f"/lockers/{locker.id}/compartments", headers=AUTH)
    assert resp.status_code == 200
    by_size = {row["size"]: row for row in resp.json()}
    assert by_size["S"] == {"size": "S", "total": 1, "occupied": 1}
    assert by_size["M"]["occupied"] == 0


def test_redirect_round_trip_between_two_lockers(
    client: TestClient, locker: Locker, db: Session
) -> None:
    other = Locker(site="Main St", active=True)
    db.add(other)
    db.flush()
    db.add(Compartment(locker_id=other.id, size="S", occupied=False))
    db.commit()

    deposited = client.post(f"/lockers/{locker.id}/parcels", json=DEPOSIT_BODY, headers=AUTH)
    parcel_id = deposited.json()["id"]
    original_code = deposited.json()["pickup_code"]

    resp = client.post(
        f"/lockers/{locker.id}/parcels/{parcel_id}/redirect",
        json={"code": original_code, "target_locker_id": other.id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pickup_code"] != original_code

    pickup = client.post(f"/lockers/{other.id}/pickup", json={"code": body["pickup_code"]})
    assert pickup.status_code == 200
