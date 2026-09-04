from conftest import CUSTOMER_KEY

H = {"X-API-Key": CUSTOMER_KEY}


def _address(label="Home", **extra):
    return {
        "label": label,
        "line1": "1 Market St",
        "city": "San Francisco",
        "postal_code": "94105",
        **extra,
    }


def test_create_address_returns_allowlisted_fields(client):
    r = client.post("/customers/me/addresses", json=_address(), headers=H)
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"id", "label", "line1", "city", "postal_code", "region", "is_default"}
    assert body["region"] == "US-CA"
    assert body["is_default"] is True
    assert client.post("/customers/me/addresses", json=_address()).status_code == 401


def test_list_and_get_addresses(client):
    first = client.post("/customers/me/addresses", json=_address(), headers=H).json()
    client.post("/customers/me/addresses", json=_address(label="Work"), headers=H)
    page = client.get("/customers/me/addresses", headers=H).json()
    assert {a["label"] for a in page["items"]} == {"Home", "Work"}
    detail = client.get(f"/customers/me/addresses/{first['id']}", headers=H)
    assert detail.status_code == 200
    assert detail.json()["label"] == "Home"


def test_order_uses_attached_address_region(client):
    address = client.post(
        "/customers/me/addresses", json=_address(label="Office", region="US-NY"), headers=H
    ).json()
    body = {
        "idempotency_key": "key-address-01",
        "items": [{"sku": "WIDGET", "quantity": 2}],
        "address_id": address["id"],
    }
    r = client.post("/orders", json=body, headers=H)
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["shipping_address"]["label"] == "Office"
    assert order["tax"] == "1.60"


def test_order_rejects_unknown_address(client):
    body = {
        "idempotency_key": "key-address-02",
        "items": [{"sku": "WIDGET", "quantity": 1}],
        "address_id": 4321,
    }
    assert client.post("/orders", json=body, headers=H).status_code == 404


def test_export_addresses_returns_csv(client):
    client.post("/customers/me/addresses", json=_address(), headers=H)
    r = client.get("/customers/me/addresses/export", headers=H)
    assert r.status_code == 200
    assert (
        r.text
        == "label,line1,city,postal_code,region\nHome,1 Market St,San Francisco,94105,US-CA\n"
    )


def test_export_addresses_with_no_addresses_returns_header_only(client):
    r = client.get("/customers/me/addresses/export", headers=H)
    assert r.status_code == 200
    assert r.text == "label,line1,city,postal_code,region\n\n"


def test_export_addresses_requires_authentication(client):
    assert client.get("/customers/me/addresses/export").status_code == 401
