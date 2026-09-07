"""Tests for sandbox/rooms/api.py."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sandbox.rooms.db import Room
from sandbox.rooms.tests.conftest import HOLDER_EMAIL, TEST_API_KEY

AUTH = {"X-API-Key": TEST_API_KEY}


def test_create_booking_requires_api_key(client: TestClient, room: Room) -> None:
    resp = client.post(
        "/bookings",
        json={"room_id": room.id, "start": "2026-09-08T09:00:00Z", "end": "2026-09-08T10:00:00Z"},
    )
    assert resp.status_code == 401


def test_create_and_get_booking(client: TestClient, room: Room) -> None:
    resp = client.post(
        "/bookings",
        json={"room_id": room.id, "start": "2026-09-08T09:00:00Z", "end": "2026-09-08T10:00:00Z"},
        headers=AUTH,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["holder_email"] == HOLDER_EMAIL
    assert body["price_cents"] == 2000

    got = client.get(f"/bookings/{body['id']}", headers=AUTH)
    assert got.status_code == 200
    assert got.json()["id"] == body["id"]


def test_cancel_forbidden_for_other_holder(client: TestClient, room: Room, monkeypatch) -> None:
    created = client.post(
        "/bookings",
        json={"room_id": room.id, "start": "2026-09-08T09:00:00Z", "end": "2026-09-08T10:00:00Z"},
        headers=AUTH,
    )
    booking_id = created.json()["id"]

    monkeypatch.setenv("ROOMS_API_KEYS", f"{HOLDER_EMAIL}:{TEST_API_KEY},eve@example.com:eve-key")
    resp = client.delete(f"/bookings/{booking_id}", headers={"X-API-Key": "eve-key"})
    assert resp.status_code == 403


def test_availability_excludes_booked_slot(client: TestClient, room: Room) -> None:
    client.post(
        "/bookings",
        json={"room_id": room.id, "start": "2026-09-08T09:00:00Z", "end": "2026-09-08T10:00:00Z"},
        headers=AUTH,
    )
    resp = client.get(f"/rooms/{room.id}/availability", params={"day": "2026-09-08"}, headers=AUTH)
    assert resp.status_code == 200
    free = resp.json()["free_slots"]
    starts = {slot["start"] for slot in free}
    assert "2026-09-08T09:00:00Z" not in starts
    assert "2026-09-08T09:30:00Z" not in starts
    assert "2026-09-08T08:30:00Z" in starts
    assert len(free) == 46
