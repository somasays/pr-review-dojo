"""Behavior pinned before and after the rewrite. These pass on both branches."""

from __future__ import annotations

import pytest

from app.api import deps
from conftest import ADMIN_KEY, CUSTOMER_KEY

H = {"X-API-Key": CUSTOMER_KEY}
A = {"X-API-Key": ADMIN_KEY}

WIDGETS = {"items": [{"sku": "WIDGET", "quantity": 2}]}
GADGETS = {"items": [{"sku": "GADGET", "quantity": 2}]}


@pytest.fixture
def sent():
    deps._sender.sent.clear()
    yield deps._sender.sent
    deps._sender.sent.clear()


def _preview(client, basket, codes=()):
    return client.post("/orders/preview", json={**basket, "discount_codes": list(codes)}, headers=H)


def test_preview_applies_a_fixed_discount_and_regional_tax(client):
    r = _preview(client, WIDGETS, ["FLAT5"])
    assert r.status_code == 200, r.text
    assert r.json() == {
        "currency": "USD",
        "subtotal": "39.98",
        "discount": "5.00",
        "tax": "2.54",
        "total": "37.52",
        "discount_code": "FLAT5",
    }


def test_preview_applies_a_threshold_discount_only_above_the_minimum(client):
    above = _preview(client, GADGETS, ["BULK15"]).json()
    assert above["subtotal"] == "240.00"
    assert above["discount"] == "36.00"
    assert above["tax"] == "14.79"
    assert above["total"] == "218.79"
    assert above["discount_code"] == "BULK15"

    below = _preview(client, WIDGETS, ["BULK15"]).json()
    assert below["discount"] == "0.00"
    assert below["discount_code"] is None
    assert below["total"] == "42.88"


def test_preview_picks_the_discount_that_saves_the_most(client):
    body = _preview(client, WIDGETS, ["WELCOME10", "FLAT5"]).json()
    assert body["discount"] == "5.00"
    assert body["discount_code"] == "FLAT5"


def test_preview_error_mapping(client):
    unknown = {"items": [{"sku": "NOSUCH", "quantity": 1}]}
    r = _preview(client, unknown)
    assert r.status_code == 422
    assert r.json()["detail"] == "unknown skus: NOSUCH"

    out_of_stock = _preview(client, {"items": [{"sku": "GIZMO", "quantity": 1}]})
    assert out_of_stock.status_code == 409
    assert out_of_stock.json()["detail"] == "GIZMO: requested 1, available 0"

    bad_code = _preview(client, WIDGETS, ["NOPE"])
    assert bad_code.status_code == 422
    assert bad_code.json()["detail"] == "NOPE"


def test_preview_writes_nothing(client):
    for _ in range(3):
        assert _preview(client, {"items": [{"sku": "GADGET", "quantity": 5}]}).status_code == 200
    assert client.get("/orders", headers=H).json()["items"] == []


def test_preview_matches_the_price_of_a_created_order(client):
    quote = _preview(client, WIDGETS, ["FLAT5"]).json()
    order = client.post(
        "/orders",
        json={"idempotency_key": "key-00000001", **WIDGETS, "discount_codes": ["FLAT5"]},
        headers=H,
    ).json()
    for key in ("currency", "subtotal", "discount", "tax", "total", "discount_code"):
        assert quote[key] == order[key], key


def _create(client, key, basket=None, codes=()):
    return client.post(
        "/orders",
        json={"idempotency_key": key, **(basket or WIDGETS), "discount_codes": list(codes)},
        headers=H,
    ).json()


def test_reorder_copies_items_and_the_discount_code(client):
    first = _create(client, "key-00000001", codes=["FLAT5"])
    again = client.post(
        f"/orders/{first['id']}/reorder", json={"idempotency_key": "key-00000002"}, headers=H
    )
    assert again.status_code == 201, again.text
    body = again.json()
    assert body["id"] != first["id"]
    assert body["items"] == first["items"]
    assert body["discount_code"] == "FLAT5"
    assert body["total"] == first["total"]
    assert body["status"] == "pending_payment"


def test_reorder_is_idempotent_and_notifies_once(client, sent):
    first = _create(client, "key-00000001")
    payload = {"idempotency_key": "key-00000002"}
    once = client.post(f"/orders/{first['id']}/reorder", json=payload, headers=H).json()
    twice = client.post(f"/orders/{first['id']}/reorder", json=payload, headers=H).json()
    assert once["id"] == twice["id"]
    assert len(sent) == 1
    message = sent[0]
    assert message.to == "ada@example.com"
    assert message.subject == f"Order {once['id']} placed from order {first['id']}"
    assert message.body == f"We are preparing the same items again. Your total is {once['total']}."
    assert message.dedupe_key == f"order-reordered:{once['id']}"


def test_reorder_is_scoped_to_the_owner_and_open_to_admins(client):
    first = _create(client, "key-00000001")
    assert (
        client.post(
            "/orders/999/reorder", json={"idempotency_key": "key-00000009"}, headers=H
        ).status_code
        == 404
    )
    as_admin = client.post(
        f"/orders/{first['id']}/reorder", json={"idempotency_key": "key-00000003"}, headers=A
    )
    assert as_admin.status_code == 201, as_admin.text
    assert as_admin.json()["id"] != first["id"]


def test_reorder_reserves_stock(client):
    first = _create(client, "key-00000001", GADGETS)
    ok = client.post(
        f"/orders/{first['id']}/reorder", json={"idempotency_key": "key-00000002"}, headers=H
    )
    assert ok.status_code == 201, ok.text
    short = client.post(
        f"/orders/{first['id']}/reorder", json={"idempotency_key": "key-00000003"}, headers=H
    )
    assert short.status_code == 409
    assert short.json()["detail"] == "GADGET: requested 2, available 1"
