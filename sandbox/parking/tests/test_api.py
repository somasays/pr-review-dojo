"""Tests for sandbox/parking/api.py."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from sandbox.parking.db import Garage
from sandbox.parking.tests.conftest import ATTENDANT_KEY, EPOCH

HEADERS = {"X-Attendant-Key": ATTENDANT_KEY}


def test_missing_attendant_key_is_rejected(client: TestClient, garage: Garage) -> None:
    response = client.get(f"/garages/{garage.id}")
    assert response.status_code == 401


def test_invalid_attendant_key_is_rejected(client: TestClient, garage: Garage) -> None:
    response = client.get(f"/garages/{garage.id}", headers={"X-Attendant-Key": "wrong"})
    assert response.status_code == 401


def test_full_enter_pay_exit_flow_updates_open_count(client: TestClient, garage: Garage) -> None:
    entered_at = EPOCH.isoformat()
    enter = client.post(
        f"/garages/{garage.id}/tickets",
        headers=HEADERS,
        json={"plate": "ABC123", "now": entered_at},
    )
    assert enter.status_code == 201
    ticket_id = enter.json()["id"]

    mid_flight = client.get(f"/garages/{garage.id}", headers=HEADERS)
    assert mid_flight.json() == {"name": "Downtown", "capacity": 2, "open_count": 1}

    pay = client.post(
        f"/tickets/{ticket_id}/pay",
        headers=HEADERS,
        json={"now": (EPOCH + timedelta(minutes=30)).isoformat()},
    )
    assert pay.status_code == 200
    assert pay.json()["status"] == "paid"

    exit_response = client.post(
        f"/tickets/{ticket_id}/exit",
        headers=HEADERS,
        json={"now": (EPOCH + timedelta(minutes=35)).isoformat()},
    )
    assert exit_response.status_code == 200
    assert exit_response.json()["status"] == "exited"

    after = client.get(f"/garages/{garage.id}", headers=HEADERS)
    assert after.json()["open_count"] == 0
