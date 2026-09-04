"""Hidden tests for exercise 05."""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import event

from conftest import ADMIN_KEY, CUSTOMER_KEY

A = {"X-API-Key": ADMIN_KEY}
H = {"X-API-Key": CUSTOMER_KEY}

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def admin_router_source() -> str:
    return (REPO_ROOT / "app" / "api" / "routers" / "admin.py").read_text()


@pytest.fixture
def tests_source() -> str:
    return (REPO_ROOT / "tests" / "test_api_admin_orders.py").read_text()


@pytest.fixture
def statements(session_factory) -> Iterator[list[tuple[str, object]]]:
    seen: list[tuple[str, object]] = []
    engine = session_factory.kw["bind"]

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        seen.append((statement, parameters))

    yield seen
    event.remove(engine, "before_cursor_execute", _capture)


def _create_order(client, key, quantity=1):
    r = client.post(
        "/orders",
        json={"idempotency_key": key, "items": [{"sku": "WIDGET", "quantity": quantity}]},
        headers=H,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _orders_select(statements):
    """The SELECT that reads the orders page, not the count and not the items load."""
    for statement, parameters in statements:
        if "FROM orders" in statement and "LIMIT" in statement and "count(" not in statement:
            return statement, parameters
    raise AssertionError(f"no orders page select captured: {[s for s, _ in statements]}")


def test_on_behalf_of_is_rejected_for_customer_keys(client, db):
    """FA-01: a customer key must not be able to act as another customer."""
    from app.db.models import Customer

    bob = Customer(email="bob@example.com", name="Bob", region="US-NY")
    db.add(bob)
    db.commit()

    impersonated = client.get("/customers/me", headers={**H, "X-On-Behalf-Of": str(bob.id)})
    assert impersonated.status_code == 403, impersonated.text

    as_admin = client.get("/customers/me", headers={**A, "X-On-Behalf-Of": str(bob.id)})
    assert as_admin.status_code == 200
    assert as_admin.json()["email"] == "bob@example.com"


def test_admin_order_list_hides_credential_material(client):
    """FA-02: the embedded customer must not carry the API key hash."""
    _create_order(client, "hidden-0000001")
    r = client.get("/admin/orders", headers=A)
    assert r.status_code == 200, r.text
    assert "api_key_hash" not in r.text
    for row in r.json()["items"]:
        assert "api_key_hash" not in row["customer"]


def test_admin_order_list_honors_offset(client):
    """FA-09 and TR-01: the second page must not repeat the first. The shipped test only
    covers the customer field and the filters and never pages, so this is the risky path
    it missed."""
    ids = {_create_order(client, f"hidden-000000{i}") for i in range(2, 5)}
    first = client.get("/admin/orders?limit=2&offset=0", headers=A).json()
    second = client.get("/admin/orders?limit=2&offset=2", headers=A).json()
    assert [o["id"] for o in first["items"]] != [o["id"] for o in second["items"]]
    assert len(second["items"]) == 1
    assert second["items"][0]["id"] not in {o["id"] for o in first["items"]}
    assert {o["id"] for o in first["items"]} | {o["id"] for o in second["items"]} == ids


def test_admin_order_list_caps_the_page_size(client, statements):
    """FA-13: the configured maximum still bounds the query."""
    _create_order(client, "hidden-0000005")
    r = client.get("/admin/orders?limit=100000", headers=A)
    assert r.status_code == 200, r.text
    assert r.json()["limit"] == 200
    _, parameters = _orders_select(statements)
    assert 200 in tuple(parameters), parameters
    assert 100000 not in tuple(parameters), parameters


def test_admin_router_does_not_build_queries(admin_router_source):
    """DS-05: the router delegates to a repository, it does not compose SQLAlchemy itself."""
    for node in ast.walk(ast.parse(admin_router_source)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = {n.name for n in node.names}
            module = getattr(node, "module", None) or ""
            assert "sqlalchemy" not in module and "sqlalchemy" not in names, ast.dump(node)
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"execute", "scalars"}, ast.dump(node)


def test_daily_counts_uses_date_range_not_a_hand_rolled_loop(admin_router_source):
    """DS-08: day iteration goes through DateRange, not a while loop that reimplements it."""
    tree = ast.parse(admin_router_source)
    assert "DateRange" in admin_router_source
    for node in ast.walk(tree):
        assert not isinstance(node, ast.While), ast.dump(node)


def test_daily_order_counts_has_a_test(tests_source):
    """DS-22 (refactor): the new public endpoint function is referenced by a shipped test."""
    assert "daily_order_counts" in tests_source or "daily-counts" in tests_source
