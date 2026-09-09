from datetime import date, timedelta

from conftest import ADMIN_KEY, CUSTOMER_KEY

A = {"X-API-Key": ADMIN_KEY}
H = {"X-API-Key": CUSTOMER_KEY}


def _create_order(client, key="admin-list-0001", quantity=1):
    r = client.post(
        "/orders",
        json={"idempotency_key": key, "items": [{"sku": "WIDGET", "quantity": quantity}]},
        headers=H,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_admin_order_list_requires_an_admin_key(client):
    assert client.get("/admin/orders").status_code == 401
    assert client.get("/admin/orders", headers=H).status_code == 403


def test_admin_order_list_shows_the_customer_of_each_order(client):
    oid = _create_order(client)
    r = client.get("/admin/orders", headers=A)
    assert r.status_code == 200, r.text
    body = r.json()
    row = next(o for o in body["items"] if o["id"] == oid)
    assert row["customer"]["email"] == "ada@example.com"
    assert row["customer_id"] == row["customer"]["id"]
    assert body["item_count"] == 1
    assert body["total"] == 1
    assert body["max_limit"] == 200


def test_admin_order_list_filters(client, seeded):
    oid = _create_order(client)
    customer_id = seeded["customer"].id
    r = client.get(f"/admin/orders?status=pending_payment&customer_id={customer_id}", headers=A)
    assert r.status_code == 200, r.text
    assert [o["id"] for o in r.json()["items"]] == [oid]
    assert client.get("/admin/orders?status=shipped", headers=A).json()["items"] == []
    assert client.get("/admin/orders?customer_id=9999", headers=A).status_code == 404


def test_admin_order_list_created_since(client):
    oid = _create_order(client)
    today = date.today().isoformat()
    r = client.get(f"/admin/orders?created_since={today}", headers=A)
    assert [o["id"] for o in r.json()["items"]] == [oid]
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    empty = client.get(f"/admin/orders?created_since={tomorrow}", headers=A).json()
    assert empty["items"] == []
    assert empty["total"] == 0
