from conftest import ADMIN_KEY, CUSTOMER_KEY


def test_me_requires_key(client):
    assert client.get("/customers/me").status_code == 401
    assert client.get("/customers/me", headers={"X-API-Key": "bogus"}).status_code == 401


def test_me_returns_allowlisted_fields(client):
    r = client.get("/customers/me", headers={"X-API-Key": CUSTOMER_KEY})
    assert r.status_code == 200
    assert set(r.json()) == {"id", "email", "name", "region", "created_at"}


def test_list_is_admin_only(client):
    assert client.get("/customers", headers={"X-API-Key": CUSTOMER_KEY}).status_code == 403
    r = client.get("/customers?limit=1", headers={"X-API-Key": ADMIN_KEY})
    assert r.status_code == 200
    assert r.json()["limit"] == 1
    assert len(r.json()["items"]) == 1


def test_create_customer_validates_and_conflicts(client):
    h = {"X-API-Key": ADMIN_KEY}
    bad = client.post("/customers", json={"email": "not-an-email", "name": "x"}, headers=h)
    assert bad.status_code == 422
    ok = client.post("/customers", json={"email": "bob@example.com", "name": "Bob"}, headers=h)
    assert ok.status_code == 201
    dup = client.post("/customers", json={"email": "bob@example.com", "name": "Bob"}, headers=h)
    assert dup.status_code == 409
