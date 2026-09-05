import pytest

from app.db.models import Customer, CustomerAddress
from app.db.repositories import CustomerRepository, NotFound
from conftest import ADMIN_KEY, CUSTOMER_KEY


def _add(db, name: str, region: str) -> Customer:
    row = Customer(email=f"{name.lower()}@example.com", name=name, region=region)
    db.add(row)
    db.commit()
    return row


def test_search_matches_name_prefix_inside_the_regions(db, seeded):
    repo = CustomerRepository(db)
    _add(db, "Nina", "US-CA")
    _add(db, "Noel", "US-NY")
    _add(db, "Bruce", "US-CA")

    rows = repo.search("N", ["US-CA", "US-NY"])

    assert [r.name for r in rows] == ["Nina", "Noel"]
    assert repo.search_count("N", ["US-CA", "US-NY"]) == 2


def test_search_region_filter_narrows_the_result(db, seeded):
    repo = CustomerRepository(db)
    _add(db, "Nina", "US-CA")
    _add(db, "Noel", "US-NY")

    rows = repo.search("N", ["US-NY"])

    assert [r.name for r in rows] == ["Noel"]
    assert repo.search_count("N", ["US-NY"]) == 1


def test_search_pages_through_the_matches(db, seeded):
    repo = CustomerRepository(db)
    for name in ["Nina", "Noel", "Nora"]:
        _add(db, name, "US-CA")

    first = repo.search("N", ["US-CA"], limit=2, offset=0)
    second = repo.search("N", ["US-CA"], limit=2, offset=2)

    assert [r.name for r in first] == ["Nina", "Noel"]
    assert [r.name for r in second] == ["Nora"]
    assert repo.search_count("N", ["US-CA"]) == 3


def test_first_match_raises_when_nothing_matches(db, seeded):
    repo = CustomerRepository(db)
    _add(db, "Nina", "US-CA")

    assert repo.first_match("N").name == "Nina"
    with pytest.raises(NotFound):
        repo.first_match("Zz")


def test_search_endpoint_is_admin_only(client):
    r = client.get("/customers/search?q=N", headers={"X-API-Key": CUSTOMER_KEY})
    assert r.status_code == 403


def test_search_endpoint_returns_a_counted_page(client):
    headers = {"X-API-Key": ADMIN_KEY}
    client.post(
        "/customers",
        json={"email": "nina@example.com", "name": "Nina", "region": "US-CA"},
        headers=headers,
    )
    client.post(
        "/customers",
        json={"email": "bruce@example.com", "name": "Bruce", "region": "US-CA"},
        headers=headers,
    )

    r = client.get("/customers/search?q=N&region=US-CA&limit=10", headers=headers)

    assert r.status_code == 200
    body = r.json()
    assert [c["name"] for c in body["items"]] == ["Nina"]
    assert body["total"] == 1
    assert body["limit"] == 10
    assert body["offset"] == 0
    assert set(body["items"][0]) == {"id", "email", "name", "region", "created_at"}


def test_lookup_endpoint_returns_404_for_no_match(client):
    headers = {"X-API-Key": ADMIN_KEY}
    assert client.get("/customers/lookup?q=Ada", headers=headers).status_code == 200
    assert client.get("/customers/lookup?q=Zz", headers=headers).status_code == 404


def test_default_address_and_import_have_basic_coverage(db, seeded):
    repo = CustomerRepository(db)
    customer_id = seeded["customer"].id

    first = repo.get_default_address(customer_id, CustomerAddress(line1="1 Main St"))
    second = repo.get_default_address(customer_id, CustomerAddress(line1="2 Oak Ave"))
    db.refresh(first)
    assert first.is_default is False
    assert second.is_default is True

    skipped = repo.import_many([Customer(email="nina@example.com", name="Nina", region="US-CA")])
    assert skipped == []
