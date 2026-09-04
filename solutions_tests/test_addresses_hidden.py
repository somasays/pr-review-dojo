"""Hidden tests for exercise 17."""

import ast
import inspect

import pytest
from sqlalchemy import event

from conftest import ADMIN_KEY, CUSTOMER_KEY

H = {"X-API-Key": CUSTOMER_KEY}
A = {"X-API-Key": ADMIN_KEY}


def _address(label="Home", **extra):
    return {
        "label": label,
        "line1": "1 Market St",
        "city": "San Francisco",
        "postal_code": "94105",
        **extra,
    }


def _other_customer_address(db):
    from app.db.models import Address, Customer

    bob = Customer(email="bob@example.com", name="Bob", region="US-NY")
    db.add(bob)
    db.flush()
    address = Address(
        customer_id=bob.id,
        label="Bob home",
        line1="99 Secret Ave",
        city="Albany",
        postal_code="12207",
        region="US-NY",
    )
    db.add(address)
    db.commit()
    return address


def test_address_detail_is_scoped_to_the_caller(client, db):
    """FA-03: another customer's address must not be readable."""
    address = _other_customer_address(db)
    r = client.get(f"/customers/me/addresses/{address.id}", headers=H)
    assert r.status_code == 404, r.text


def test_set_default_is_scoped_to_the_caller(client, db):
    """FA-03: another customer's address must not be writable."""
    address = _other_customer_address(db)
    r = client.post(f"/customers/me/addresses/{address.id}/default", headers=H)
    assert r.status_code == 404, r.text
    db.expire_all()
    assert db.get(type(address), address.id).is_default is False


def test_revoked_api_key_stops_working(client, db, seeded):
    """FA-04: the principal must not be memoized across requests."""
    assert client.get("/customers/me", headers=H).status_code == 200
    seeded["customer"].api_key_hash = None
    db.commit()
    assert client.get("/customers/me", headers=H).status_code == 401


def test_create_address_is_not_a_coroutine_handler():
    """FA-07: a handler doing synchronous database work must not be async def."""
    from app.api.routers import addresses

    assert not inspect.iscoroutinefunction(addresses.create_address)


def test_default_address_endpoint_is_reachable(client):
    """FA-11: the static /default route must not be shadowed by /{address_id}."""
    created = client.post("/customers/me/addresses", json=_address(), headers=H).json()
    r = client.get("/customers/me/addresses/default", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == created["id"]


def test_address_region_is_validated(client):
    """FA-12: an unmappable region must be rejected, not silently taxed at zero."""
    r = client.post("/customers/me/addresses", json=_address(region="california"), headers=H)
    assert r.status_code == 422, r.text


def test_order_list_does_not_scale_queries_with_rows(client, session_factory):
    """FA-XC: the shipping address must be eager loaded for the order list."""
    statements: list[str] = []

    @event.listens_for(session_factory.kw["bind"], "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    for n in range(6):
        address = client.post(
            "/customers/me/addresses", json=_address(label=f"Stop {n}"), headers=H
        ).json()
        body = {
            "idempotency_key": f"hidden-key-{n:04d}",
            "items": [{"sku": "WIDGET", "quantity": 1}],
            "address_id": address["id"],
        }
        assert client.post("/orders", json=body, headers=H).status_code == 201
    statements.clear()
    r = client.get("/orders", headers=H)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 6
    assert all(o["shipping_address"] is not None for o in items)
    assert len(statements) <= 5, statements


def test_export_router_uses_the_repository_not_raw_sql():
    """DS-05: the export endpoint must go through AddressRepository, not raw SQLAlchemy."""
    from app.api.routers import addresses

    tree = ast.parse(inspect.getsource(addresses))
    imported_select = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "sqlalchemy"
        and any(alias.name == "select" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert not imported_select
    query_calls = [
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in {"scalars", "execute"}
    ]
    assert not query_calls, query_calls


def test_export_does_not_read_settings_directly():
    """DS-10: settings must be injected, not read with get_settings() inside the handler."""
    from app.api.routers import addresses

    tree = ast.parse(inspect.getsource(addresses))
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "get_settings" not in calls


def test_csv_formatting_is_a_pure_function():
    """DS-21 refactor: CSV formatting should be extractable and callable without a session."""
    from app.api.routers.addresses import format_addresses_csv

    class Row:
        def __init__(self, label: str) -> None:
            self.label = label
            self.line1 = "1 Market St"
            self.city = "SF"
            self.postal_code = "94105"
            self.region = "US-CA"

    out = format_addresses_csv([Row("Home")])
    assert out.startswith("label,line1,city,postal_code,region\n")
    assert "Home" in out


@pytest.mark.parametrize("key", [A])
def test_admin_key_has_no_address_book(client, key):
    """Sanity: admin keys carry no customer context."""
    assert client.get("/customers/me/addresses", headers=key).status_code == 403
