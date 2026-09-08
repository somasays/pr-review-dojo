"""Tests for sandbox/expenses/api.py."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sandbox.expenses.tests.conftest import APPROVER_KEY, EMPLOYEE_KEY, OTHER_EMPLOYEE_KEY

EMPLOYEE_AUTH = {"X-Expenses-Key": EMPLOYEE_KEY}
APPROVER_AUTH = {"X-Expenses-Key": APPROVER_KEY}
OTHER_EMPLOYEE_AUTH = {"X-Expenses-Key": OTHER_EMPLOYEE_KEY}

CLAIM_BODY = {
    "idempotency_key": "api-key-1",
    "currency": "USD",
    "lines": [{"category": "meals", "amount": "20.00", "incurred_on": "2026-06-15"}],
}


def test_create_claim_requires_employee_role(client: TestClient) -> None:
    resp = client.post("/claims", json=CLAIM_BODY, headers=APPROVER_AUTH)
    assert resp.status_code == 403


def test_create_claim_as_employee(client: TestClient) -> None:
    resp = client.post("/claims", json=CLAIM_BODY, headers=EMPLOYEE_AUTH)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "submitted"
    assert len(body["lines"]) == 1


def test_get_claim_owner_and_approver_allowed_others_forbidden(client: TestClient) -> None:
    claim_id = client.post("/claims", json=CLAIM_BODY, headers=EMPLOYEE_AUTH).json()["id"]

    assert client.get(f"/claims/{claim_id}", headers=EMPLOYEE_AUTH).status_code == 200
    assert client.get(f"/claims/{claim_id}", headers=APPROVER_AUTH).status_code == 200
    assert client.get(f"/claims/{claim_id}", headers=OTHER_EMPLOYEE_AUTH).status_code == 403


def test_decision_endpoint_requires_approver_role(client: TestClient) -> None:
    claim_id = client.post("/claims", json=CLAIM_BODY, headers=EMPLOYEE_AUTH).json()["id"]
    body = {"approve": True}
    resp = client.post(f"/claims/{claim_id}/decision", json=body, headers=EMPLOYEE_AUTH)
    assert resp.status_code == 403


def test_payouts_batches_creates_batch(client: TestClient) -> None:
    claim_id = client.post("/claims", json=CLAIM_BODY, headers=EMPLOYEE_AUTH).json()["id"]
    client.post(f"/claims/{claim_id}/decision", json={"approve": True}, headers=APPROVER_AUTH)

    resp = client.post("/payouts/batches", headers=APPROVER_AUTH)
    assert resp.status_code == 201
    assert resp.json()["count"] == 1
