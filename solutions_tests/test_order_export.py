"""Hidden tests for exercise 23."""

import ast
import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db.models import Customer, Order, OrderItem, Product
from app.db.repositories import OrderRepository
from app.services.config import get_settings
from app.services.export_service import ExportFilters, build_export

WINDOW = timedelta(days=7)


def _order(
    db: Session,
    customer_id: int,
    key: str,
    status: str = "paid",
    created_at: datetime | None = None,
    product_ids: tuple[int, ...] = (),
) -> Order:
    order = Order(
        customer_id=customer_id,
        idempotency_key=key,
        status=status,
        subtotal=Decimal("10.00"),
        total=Decimal("10.00"),
        created_at=created_at or datetime.now(tz=UTC),
    )
    order.items = [
        OrderItem(product_id=pid, sku=f"SKU-{pid}", quantity=1, unit_price=Decimal("10.00"))
        for pid in product_ids
    ]
    db.add(order)
    db.commit()
    return order


def _filters(statuses: list[str], anchor: datetime, limit: int = 50, cursor=None) -> ExportFilters:
    return ExportFilters(
        statuses=statuses,
        start=anchor - WINDOW,
        end=anchor + WINDOW,
        limit=limit,
        cursor=cursor,
    )


def _capture(engine) -> list[str]:
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    return statements


def test_export_summary_binds_the_status_filter(db, seeded, engine):
    """SA-01: a crafted status value must not change the WHERE clause."""
    customer = seeded["customer"]
    other = Customer(email="grace@example.com", name="Grace", region="US-CA")
    db.add(other)
    db.commit()
    now = datetime.now(tz=UTC)
    _order(db, customer.id, "inject-1")
    _order(db, other.id, "inject-2")

    statements = _capture(engine)
    payload = "paid') OR 1=1 OR status IN ('paid"
    count, _gross = OrderRepository(db).export_summary(
        customer.id, [payload], now - WINDOW, now + WINDOW
    )

    assert count == 0
    assert not any("OR 1=1" in s for s in statements)


def test_export_handler_opens_a_session_per_call(monkeypatch, session_factory, db, seeded):
    """SA-06: the worker handler must not share one Session across threads."""
    from app.async_tasks import handlers

    assert not hasattr(handlers, "_session")

    made: list[Session] = []

    def _factory() -> Session:
        session = session_factory()
        made.append(session)
        return session

    monkeypatch.setattr(handlers, "_factory", _factory)
    customer = seeded["customer"]
    _order(db, customer.id, "handler-1")

    payload = {"customer_id": customer.id, "statuses": ["paid"], "days": 1, "limit": 10}
    first = handlers.export_orders(payload)
    second = handlers.export_orders(payload)

    assert first["rows"] == 1
    assert second["rows"] == 1
    assert len(made) == 2
    assert made[0] is not made[1]


def test_export_rows_do_not_query_once_per_product(db, seeded, session_factory, engine):
    """SA-07: product names come from one extra SELECT, not one per line item."""
    customer = seeded["customer"]
    products = [
        Product(sku=f"BULK-{i}", name=f"Bulk {i}", unit_price=Decimal("10.00"), stock=10)
        for i in range(10)
    ]
    db.add_all(products)
    db.commit()
    ids = [p.id for p in products]
    now = datetime.now(tz=UTC)
    for i in range(5):
        _order(db, customer.id, f"n1-{i}", product_ids=(ids[i * 2], ids[i * 2 + 1]))

    fresh = session_factory()
    statements = _capture(engine)
    page = build_export(fresh, customer.id, _filters(["paid"], now))
    fresh.close()

    assert len(page.rows) == 5
    assert all(row.products for row in page.rows)
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) <= 6, f"{len(selects)} SELECT statements for 5 orders"


def test_cursor_pages_return_every_order_once(db, seeded, engine):
    """SA-09: the page order needs a unique tiebreaker, or pages skip orders."""
    customer = seeded["customer"]
    anchor = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    expected = {_order(db, customer.id, f"tie-{i}", created_at=anchor).id for i in range(5)}

    statements = _capture(engine)
    seen: list[int] = []
    cursor = None
    for _ in range(6):
        page = build_export(db, customer.id, _filters(["paid"], anchor, limit=2, cursor=cursor))
        seen.extend(row.order_id for row in page.rows)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert len(seen) == len(set(seen)), f"pages repeated orders: {seen}"
    assert set(seen) == expected
    ordered = [s for s in statements if "FROM orders" in s and "ORDER BY" in s]
    assert ordered, "no page query was captured"
    for statement in ordered:
        assert "orders.id" in statement.split("ORDER BY", 1)[1]


def test_export_limit_is_capped_by_page_size_max(db, seeded, monkeypatch):
    """SA-XC: the export honours PAGE_SIZE_MAX like every other list endpoint."""
    customer = seeded["customer"]
    now = datetime.now(tz=UTC)
    for i in range(5):
        _order(db, customer.id, f"cap-{i}")

    monkeypatch.setenv("PAGE_SIZE_MAX", "2")
    get_settings.cache_clear()
    try:
        page = build_export(db, customer.id, _filters(["paid"], now, limit=50))
        assert len(page.rows) == 2
        assert page.next_cursor is not None
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_notify_export_ready_survives_closed_session(monkeypatch, session_factory, db, seeded):
    """SA-13: the customer's email must be read before the session closes."""
    from app.async_tasks import handlers
    from app.services.notification import InMemorySender, NotificationService

    monkeypatch.setattr(handlers, "_factory", session_factory)
    customer = seeded["customer"]
    order = _order(db, customer.id, "notify-1")

    sender = InMemorySender()
    notifications = NotificationService(sender)
    handlers.notify_export_ready({"order_id": order.id}, notifications)

    assert len(sender.sent) == 1
    assert sender.sent[0].to == customer.email


def test_notify_export_ready_takes_an_injected_notifier():
    """DS-04: the handler depends on the Sender seam, not a concrete sender."""
    from app.async_tasks import handlers

    source = inspect.getsource(handlers)
    assert "InMemorySender" not in source
    sig = inspect.signature(handlers.notify_export_ready)
    assert sig.parameters["notifications"].annotation == "NotificationService"


def test_notify_export_ready_has_no_hand_rolled_retry():
    """DS-08: retries live in NotificationService, not a local loop."""
    from app.async_tasks import handlers

    tree = ast.parse(inspect.getsource(handlers))
    loops = [n for n in ast.walk(tree) if isinstance(n, ast.For)]
    assert not loops, "handlers.py should not hand-roll a retry loop"


def test_export_window_is_built_in_one_place():
    """DS-20: the endpoint and the queue handler share one window builder."""
    from app.api.routers import reports
    from app.async_tasks import handlers

    for module in (reports, handlers):
        tree = ast.parse(inspect.getsource(module))
        calls = {
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "ExportFilters" not in calls, f"{module.__name__} builds filters directly"
        assert "window_filters" in calls, f"{module.__name__} does not share the window builder"


def test_notify_export_ready_has_no_boolean_flag():
    """Refactor (DS-11): one behavior per function, no dry_run switch."""
    from app.async_tasks import handlers

    sig = inspect.signature(handlers.notify_export_ready)
    assert not any(p.annotation == "bool" for p in sig.parameters.values())
