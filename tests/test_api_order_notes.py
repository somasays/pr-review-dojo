from conftest import ADMIN_KEY, CUSTOMER_KEY

H = {"X-API-Key": CUSTOMER_KEY}
A = {"X-API-Key": ADMIN_KEY}


def _create_order(client, key="note-00000001"):
    r = client.post(
        "/orders",
        json={"idempotency_key": key, "items": [{"sku": "WIDGET", "quantity": 1}]},
        headers=H,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_new_order_has_no_notes(client):
    oid = _create_order(client)
    assert client.get(f"/orders/{oid}", headers=H).json()["notes"] == []


def test_customer_can_add_a_note(client):
    oid = _create_order(client)
    r = client.patch(f"/orders/{oid}/notes", json={"body": "leave at the back door"}, headers=H)
    assert r.status_code == 200, r.text
    notes = r.json()["notes"]
    assert len(notes) == 1
    assert notes[0]["body"] == "leave at the back door"
    assert notes[0]["author"].startswith("customer:")


def test_notes_are_appended_in_order(client):
    oid = _create_order(client)
    client.patch(f"/orders/{oid}/notes", json={"body": "first"}, headers=H)
    client.patch(f"/orders/{oid}/notes", json={"body": "second"}, headers=H)
    bodies = [n["body"] for n in client.get(f"/orders/{oid}", headers=H).json()["notes"]]
    assert bodies == ["first", "second"]


def test_admin_note_is_attributed_to_admin(client):
    oid = _create_order(client)
    r = client.patch(f"/orders/{oid}/notes", json={"body": "refund promised"}, headers=A)
    assert r.status_code == 200, r.text
    assert r.json()["notes"][0]["author"] == "admin"


def test_note_on_unknown_order_is_404(client):
    assert client.patch("/orders/999/notes", json={"body": "nope"}, headers=H).status_code == 404


def test_note_body_of_500_chars_is_accepted_501_is_rejected(client):
    oid = _create_order(client)
    empty = client.patch(f"/orders/{oid}/notes", json={"body": ""}, headers=H)
    assert empty.status_code == 422
    at_limit = client.patch(f"/orders/{oid}/notes", json={"body": "x" * 500}, headers=H)
    assert at_limit.status_code == 200
    over_limit = client.patch(f"/orders/{oid}/notes", json={"body": "x" * 501}, headers=H)
    assert over_limit.status_code == 422


def test_notes_are_listed_with_the_order(client):
    oid = _create_order(client)
    client.patch(f"/orders/{oid}/notes", json={"body": "gift wrap"}, headers=H)
    page = client.get("/orders", headers=H).json()
    assert page["items"][0]["notes"][0]["body"] == "gift wrap"
