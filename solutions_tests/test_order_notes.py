"""Hidden tests for exercise 01: order notes."""

import ast
import inspect

import app.api.routers.orders as orders_module
from app.db.models import Order
from conftest import CUSTOMER_KEY

H = {"X-API-Key": CUSTOMER_KEY}


def _create_order(client, key="hid-00000001"):
    r = client.post(
        "/orders",
        json={"idempotency_key": key, "items": [{"sku": "WIDGET", "quantity": 1}]},
        headers=H,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_listing_orders_loads_notes_in_one_query(client, session_factory):
    """Notes must not add a query per order to the list endpoint."""
    from sqlalchemy import event

    for i in range(10):
        _create_order(client, key=f"npo-{i:08d}")

    statements: list[str] = []
    engine = session_factory.kw["bind"]

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        r = client.get("/orders?limit=50", headers=H)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 10
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) <= 5, selects


def test_writes_survive_the_real_session_dependency(session_factory, seeded, monkeypatch):
    """get_db must commit on success, not just close the session."""
    import app.api.deps as deps_module
    from app.api.main import create_app

    monkeypatch.setattr(deps_module, "get_session_factory", lambda: session_factory)
    app = create_app()
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        r = client.post(
            "/orders",
            json={
                "idempotency_key": "real-session-001",
                "items": [{"sku": "WIDGET", "quantity": 1}],
            },
            headers=H,
        )
    assert r.status_code == 201, r.text
    fresh = session_factory()
    try:
        assert fresh.get(Order, r.json()["id"]) is not None
    finally:
        fresh.close()


def test_note_cannot_be_added_to_a_cancelled_order(client, db):
    """A closed order should answer 409, not 500, when a note is attempted."""
    oid = _create_order(client, key="cancel-note-001")
    cancelled = client.post(f"/orders/{oid}/cancel", headers=H)
    assert cancelled.status_code == 200, cancelled.text
    r = client.patch(f"/orders/{oid}/notes", json={"body": "too late"}, headers=H)
    assert r.status_code == 409, r.text
    db.expire_all()
    assert db.get(Order, oid).notes == []


def test_note_handler_delegates_to_the_order_service():
    """The handler should not write through the repository or the session itself."""
    src = inspect.getsource(orders_module.add_order_note)
    tree = ast.parse(src)
    calls_on_service = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "service"
    }
    other_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and not (isinstance(node.func.value, ast.Name) and node.func.value.id == "service")
    }
    assert "add_note" in calls_on_service
    assert "add_note" not in other_calls
    assert "commit" not in other_calls
    assert "service" in inspect.signature(orders_module.add_order_note).parameters


def test_order_lookup_scoping_is_not_duplicated():
    """get_order and add_order_note must share one ownership-scoping helper."""
    assert hasattr(orders_module, "_scope_order")
    for name in ("get_order", "add_order_note"):
        src = inspect.getsource(getattr(orders_module, name))
        tree = ast.parse(src)
        names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "_scope_order" in names, f"{name} does not call the shared scoping helper"


def test_note_author_formatting_is_a_pure_function():
    """The author label should be a small function, importable without a session."""
    assert inspect.isfunction(orders_module._format_note_author)
    params = inspect.signature(orders_module._format_note_author).parameters
    assert list(params) == ["principal"]
