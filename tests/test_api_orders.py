from conftest import ADMIN_KEY, CUSTOMER_KEY

H = {"X-API-Key": CUSTOMER_KEY}
A = {"X-API-Key": ADMIN_KEY}


def _body(key="key-00000001", **extra):
    return {"idempotency_key": key, "items": [{"sku": "WIDGET", "quantity": 2}], **extra}


def test_create_order_happy_path(client):
    r = client.post("/orders", json=_body(discount_codes=["FLAT5"]), headers=H)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending_payment"
    assert body["subtotal"] == "39.98"
    assert body["discount"] == "5.00"
    assert body["items"][0]["sku"] == "WIDGET"
    assert "customer_id" not in body


def test_create_order_validation(client):
    r = client.post("/orders", json={"idempotency_key": "short", "items": []}, headers=H)
    assert r.status_code == 422
    dup = _body()
    dup["items"].append({"sku": "WIDGET", "quantity": 1})
    assert client.post("/orders", json=dup, headers=H).status_code == 422
    unknown = _body(discount_codes=["NOPE"])
    assert client.post("/orders", json=unknown, headers=H).status_code == 422


def test_create_order_out_of_stock_conflicts(client):
    body = {"idempotency_key": "key-00000002", "items": [{"sku": "GIZMO", "quantity": 1}]}
    assert client.post("/orders", json=body, headers=H).status_code == 409


def test_create_is_idempotent_over_http(client):
    first = client.post("/orders", json=_body(), headers=H).json()
    second = client.post("/orders", json=_body(), headers=H).json()
    assert first["id"] == second["id"]


def test_get_and_list_are_scoped_to_customer(client):
    oid = client.post("/orders", json=_body(), headers=H).json()["id"]
    assert client.get(f"/orders/{oid}", headers=H).status_code == 200
    assert client.get(f"/orders/{oid}", headers=A).status_code == 200
    assert client.get("/orders/999", headers=H).status_code == 404
    assert client.get("/orders", headers=H).json()["items"][0]["id"] == oid
    assert client.get("/orders", headers=A).status_code == 403


def test_lifecycle_over_http(client):
    oid = client.post("/orders", json=_body(), headers=H).json()["id"]
    assert client.post(f"/orders/{oid}/pay", headers=H).status_code == 403
    assert client.post(f"/orders/{oid}/pay", headers=A).json()["status"] == "paid"
    assert client.post(f"/orders/{oid}/cancel", headers=H).status_code == 409
    assert client.post(f"/orders/{oid}/ship", headers=A).json()["status"] == "shipped"
    assert client.get("/health").json() == {"status": "ok"}


def test_refund_over_http(client):
    oid = client.post("/orders", json=_body(key="key-00000007"), headers=H).json()["id"]
    assert client.post(f"/orders/{oid}/refund", json={}, headers=A).status_code == 409
    client.post(f"/orders/{oid}/pay", headers=A)
    refunded = client.post(f"/orders/{oid}/refund", json={"reason": "late"}, headers=A)
    assert refunded.status_code == 200, refunded.text
    assert refunded.json()["status"] == "refunded"
    assert client.post(f"/orders/{oid}/refund", json={}, headers=H).status_code == 403


def test_refund_preview_lists_every_line(client):
    oid = client.post("/orders", json=_body(key="key-00000008"), headers=H).json()["id"]
    assert client.get(f"/orders/{oid}/refund-preview", headers=H).status_code == 403
    preview = client.get(f"/orders/{oid}/refund-preview", headers=A)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["order_id"] == oid
    assert body["currency"] == "USD"
    assert [line["sku"] for line in body["lines"]] == ["WIDGET"]
    assert client.get("/orders/999/refund-preview", headers=A).status_code == 404
