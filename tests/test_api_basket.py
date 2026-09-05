from conftest import CUSTOMER_KEY

H = {"X-API-Key": CUSTOMER_KEY}
BASKET = {"items": [{"sku": "WIDGET", "quantity": 2}]}


def test_preview_prices_a_basket_without_creating_an_order(client):
    r = client.post("/orders/preview", json={**BASKET, "discount_codes": ["FLAT5"]}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "currency": "USD",
        "subtotal": "39.98",
        "discount": "5.00",
        "tax": "2.54",
        "total": "37.52",
        "discount_code": "FLAT5",
    }
    assert client.get("/orders", headers=H).json()["items"] == []


def test_reorder_copies_the_items_of_a_previous_order(client):
    first = client.post(
        "/orders", json={"idempotency_key": "key-00000001", **BASKET}, headers=H
    ).json()
    payload = {"idempotency_key": "key-00000002"}
    r = client.post(f"/orders/{first['id']}/reorder", json=payload, headers=H)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] != first["id"]
    assert body["total"] == first["total"]
    assert body["items"] == first["items"]
    again = client.post(f"/orders/{first['id']}/reorder", json=payload, headers=H).json()
    assert again["id"] == body["id"]
