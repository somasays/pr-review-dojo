"""Tests for the FastAPI app: auth roles and the checkout/return flow."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sandbox.library.tests.conftest import (
    LIBRARIAN_KEY,
    OTHER_PATRON_KEY,
    PATRON_KEY,
)


def test_missing_key_is_unauthorized(client: TestClient) -> None:
    response = client.get("/items/1")
    assert response.status_code == 401


def test_write_endpoint_requires_patron_role(client: TestClient, seeded) -> None:  # noqa: ANN001
    body = {"item_id": seeded["book"].id}
    response = client.post("/loans", json=body, headers={"X-Library-Key": LIBRARIAN_KEY})
    assert response.status_code == 403


def test_loan_visibility_by_role(client: TestClient, seeded) -> None:  # noqa: ANN001
    alice, bob = seeded["alice"], seeded["bob"]

    own = client.get(f"/patrons/{alice.id}/loans", headers={"X-Library-Key": PATRON_KEY})
    assert own.status_code == 200

    other = client.get(f"/patrons/{bob.id}/loans", headers={"X-Library-Key": PATRON_KEY})
    assert other.status_code == 403

    as_librarian = client.get(f"/patrons/{bob.id}/loans", headers={"X-Library-Key": LIBRARIAN_KEY})
    assert as_librarian.status_code == 200


def test_get_item_works_for_any_role(client: TestClient, seeded) -> None:  # noqa: ANN001
    book = seeded["book"]
    for key in (PATRON_KEY, LIBRARIAN_KEY):
        response = client.get(f"/items/{book.id}", headers={"X-Library-Key": key})
        assert response.status_code == 200
        assert response.json()["available_copies"] == 1


def test_checkout_and_return_flow(client: TestClient, seeded) -> None:  # noqa: ANN001
    book = seeded["other_book"]
    headers = {"X-Library-Key": PATRON_KEY}

    created = client.post("/loans", json={"item_id": book.id}, headers=headers)
    assert created.status_code == 201
    loan_id = created.json()["id"]
    assert created.json()["status"] == "active"

    returned = client.post(f"/loans/{loan_id}/return", headers=headers)
    assert returned.status_code == 200
    assert returned.json()["loan"]["status"] == "returned"
    assert "fine" in returned.json()

    item = client.get(f"/items/{book.id}", headers=headers)
    assert item.json()["available_copies"] == book.copies


def test_checkout_no_copies_is_conflict(client: TestClient, seeded) -> None:  # noqa: ANN001
    book = seeded["book"]  # single copy
    headers_alice = {"X-Library-Key": PATRON_KEY}
    headers_bob = {"X-Library-Key": OTHER_PATRON_KEY}

    first = client.post("/loans", json={"item_id": book.id}, headers=headers_alice)
    assert first.status_code == 201

    second = client.post("/loans", json={"item_id": book.id}, headers=headers_bob)
    assert second.status_code == 409
