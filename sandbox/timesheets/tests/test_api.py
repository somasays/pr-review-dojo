"""Tests for the FastAPI app: auth, ownership, and the request/response
shapes around the service layer."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sandbox.timesheets.tests.conftest import MANAGER_KEY, NY_WORKER_KEY, WORKER_KEY


def _shift_body(start: str, end: str) -> dict:
    return {"start_utc": start, "end_utc": end, "note": None}


def test_create_shift_requires_a_worker_key(client: TestClient) -> None:
    body = _shift_body("2026-01-05T16:00:00Z", "2026-01-05T20:00:00Z")
    unauthenticated = client.post("/shifts", json=body)
    assert unauthenticated.status_code == 401

    as_manager = client.post("/shifts", json=body, headers={"X-Timesheets-Key": MANAGER_KEY})
    assert as_manager.status_code == 403

    as_worker = client.post("/shifts", json=body, headers={"X-Timesheets-Key": WORKER_KEY})
    assert as_worker.status_code == 201
    assert as_worker.json()["minutes"] == 240


def test_submit_and_get_timesheet_as_the_owning_worker(client: TestClient) -> None:
    client.post(
        "/shifts",
        json=_shift_body("2026-01-05T16:00:00Z", "2026-01-05T20:00:00Z"),
        headers={"X-Timesheets-Key": WORKER_KEY},
    )
    submitted = client.post(
        "/timesheets/2026-01-05/submit", headers={"X-Timesheets-Key": WORKER_KEY}
    )
    assert submitted.status_code == 200
    timesheet_id = submitted.json()["id"]

    fetched = client.get(f"/timesheets/{timesheet_id}", headers={"X-Timesheets-Key": WORKER_KEY})
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "submitted"


def test_get_timesheet_forbidden_for_a_different_worker(client: TestClient) -> None:
    client.post(
        "/shifts",
        json=_shift_body("2026-01-05T16:00:00Z", "2026-01-05T20:00:00Z"),
        headers={"X-Timesheets-Key": WORKER_KEY},
    )
    submitted = client.post(
        "/timesheets/2026-01-05/submit", headers={"X-Timesheets-Key": WORKER_KEY}
    )
    timesheet_id = submitted.json()["id"]

    other_worker = client.get(
        f"/timesheets/{timesheet_id}", headers={"X-Timesheets-Key": NY_WORKER_KEY}
    )
    assert other_worker.status_code == 403

    as_manager = client.get(
        f"/timesheets/{timesheet_id}", headers={"X-Timesheets-Key": MANAGER_KEY}
    )
    assert as_manager.status_code == 200


def test_decision_requires_a_manager_key_and_updates_pending_list(client: TestClient) -> None:
    client.post(
        "/shifts",
        json=_shift_body("2026-01-05T16:00:00Z", "2026-01-05T20:00:00Z"),
        headers={"X-Timesheets-Key": WORKER_KEY},
    )
    submitted = client.post(
        "/timesheets/2026-01-05/submit", headers={"X-Timesheets-Key": WORKER_KEY}
    )
    timesheet_id = submitted.json()["id"]

    pending_before = client.get("/timesheets/pending", headers={"X-Timesheets-Key": MANAGER_KEY})
    assert any(t["id"] == timesheet_id for t in pending_before.json())

    forbidden = client.post(
        f"/timesheets/{timesheet_id}/decision",
        json={"approve": True, "reason": None},
        headers={"X-Timesheets-Key": WORKER_KEY},
    )
    assert forbidden.status_code == 403

    decided = client.post(
        f"/timesheets/{timesheet_id}/decision",
        json={"approve": True, "reason": None},
        headers={"X-Timesheets-Key": MANAGER_KEY},
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"

    pending_after = client.get("/timesheets/pending", headers={"X-Timesheets-Key": MANAGER_KEY})
    assert all(t["id"] != timesheet_id for t in pending_after.json())


def test_corrections_endpoint_creates_a_new_version(client: TestClient) -> None:
    client.post(
        "/shifts",
        json=_shift_body("2026-01-05T16:00:00Z", "2026-01-05T20:00:00Z"),
        headers={"X-Timesheets-Key": WORKER_KEY},
    )
    submitted = client.post(
        "/timesheets/2026-01-05/submit", headers={"X-Timesheets-Key": WORKER_KEY}
    )
    timesheet_id = submitted.json()["id"]
    client.post(
        f"/timesheets/{timesheet_id}/decision",
        json={"approve": True, "reason": None},
        headers={"X-Timesheets-Key": MANAGER_KEY},
    )

    corrected = client.post(
        f"/timesheets/{timesheet_id}/corrections", headers={"X-Timesheets-Key": WORKER_KEY}
    )
    assert corrected.status_code == 201
    body = corrected.json()
    assert body["version"] == 2
    assert body["status"] == "open"
