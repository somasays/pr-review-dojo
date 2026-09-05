"""Hidden tests for exercise 28: the order_events audit table."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

VERSIONS = Path("app/db/alembic/versions")
ROUTER = Path("app/api/routers/orders.py")


def _revision_source() -> str:
    for path in VERSIONS.glob("*.py"):
        src = path.read_text()
        if 'revision: str = "0003"' in src:
            return src
    raise AssertionError("no revision 0003 under app/db/alembic/versions")


@pytest.fixture
def migrated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Alembic config plus an engine pointed at a fresh on-disk SQLite database."""
    url = f"sqlite:///{tmp_path}/mig.db"
    monkeypatch.setenv("DATABASE_URL", url)
    from app.services.config import get_settings

    get_settings.cache_clear()
    cfg = Config("alembic.ini")
    engine = create_engine(url)
    yield cfg, engine
    engine.dispose()
    get_settings.cache_clear()


def _seed_order(engine, **extra: object) -> None:
    columns = {
        "customer_id": 1,
        "idempotency_key": "seed-key-1",
        "status": "paid",
        "currency": "EUR",
        "subtotal": "10.00",
        "discount": "0.00",
        "tax": "0.00",
        "total": "10.00",
        "discount_code": "WELCOME10",
        **extra,
    }
    names = ", ".join(columns)
    binds = ", ".join(f":{c}" for c in columns)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO customers (email, name, region) VALUES ('a@b.c', 'Ada', 'US-CA')")
        )
        conn.execute(text(f"INSERT INTO orders ({names}) VALUES ({binds})"), columns)


def test_downgrade_removes_only_the_column_this_revision_added(migrated) -> None:
    """MG-03: rolling back 0003 must not destroy an unrelated orders column."""
    cfg, engine = migrated
    command.upgrade(cfg, "head")
    _seed_order(engine)

    command.downgrade(cfg, "0002")

    columns = {c["name"] for c in inspect(engine).get_columns("orders")}
    assert "last_event_at" not in columns
    assert "discount_code" in columns
    with engine.connect() as conn:
        assert conn.execute(text("SELECT discount_code FROM orders")).scalar() == "WELCOME10"


def test_order_key_types_are_left_alone(migrated) -> None:
    """MG-05: 0003 must not rewrite orders or order_items to widen the id columns."""
    cfg, engine = migrated
    command.upgrade(cfg, "head")
    insp = inspect(engine)

    orders_id = next(c for c in insp.get_columns("orders") if c["name"] == "id")
    items_order_id = next(c for c in insp.get_columns("order_items") if c["name"] == "order_id")
    assert str(orders_id["type"]) == "INTEGER"
    assert str(items_order_id["type"]) == "INTEGER"


def test_revision_carries_no_data_backfill() -> None:
    """MG-08: the schema migration must not rewrite order rows in the same transaction."""
    src = _revision_source()
    assert "bulk_insert" not in src
    assert "op.execute(" not in src
    assert "sa.select(" not in src


def test_order_events_foreign_key_is_indexed(migrated) -> None:
    """MG-09: order_events.order_id needs an index of its own."""
    cfg, engine = migrated
    command.upgrade(cfg, "head")

    indexes = inspect(engine).get_indexes("order_events")
    assert any(ix["column_names"][:1] == ["order_id"] for ix in indexes), indexes


def test_orders_updated_at_survives_this_revision(migrated) -> None:
    """MG-11: dropping a column the running release still selects belongs in a later revision."""
    cfg, engine = migrated
    command.upgrade(cfg, "head")

    columns = {c["name"] for c in inspect(engine).get_columns("orders")}
    assert "updated_at" in columns


def test_creating_an_order_records_the_first_event(db: Session, seeded: dict[str, object]) -> None:
    """MG-XC: the audit trail and orders.last_event_at must cover newly created orders."""
    from app.db.models import OrderEvent
    from app.services.config import Settings
    from app.services.notification import InMemorySender, NotificationService
    from app.services.order_service import CreateOrderCommand, OrderService
    from app.services.pricing_service import ItemRequest, PricingService

    service = OrderService(db, PricingService(), NotificationService(InMemorySender(), Settings()))
    customer = seeded["customer"]
    order = service.create(
        CreateOrderCommand(
            customer_id=customer.id,  # type: ignore[attr-defined]
            idempotency_key="hidden-key-1",
            items=[ItemRequest("WIDGET", 1)],
            discount_codes=[],
        )
    )
    db.flush()

    events = db.query(OrderEvent).filter(OrderEvent.order_id == order.id).all()
    assert [(e.from_status, e.to_status) for e in events] == [(None, "pending_payment")]
    assert order.last_event_at is not None


def test_router_does_not_build_queries_directly() -> None:
    """DS-05: the recent-events report goes through a repository, not a raw select."""
    tree = ast.parse(ROUTER.read_text())
    sqlalchemy_imports = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "sqlalchemy"
        for alias in node.names
    }
    assert not sqlalchemy_imports, f"router imports from sqlalchemy: {sqlalchemy_imports}"
    stray_calls = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in {"execute", "scalars"}
    }
    assert not stray_calls, f"router calls a session query method directly: {stray_calls}"


def test_record_event_takes_a_now_parameter() -> None:
    """DS-09: the clock must be injectable so the audit stamp is deterministic in tests."""
    import inspect as inspect_module

    from app.services.order_service import OrderService

    sig = inspect_module.signature(OrderService._record_event)
    assert "now" in sig.parameters


def test_record_event_is_deterministic_with_a_fixed_now(
    db: Session, seeded: dict[str, object]
) -> None:
    """DS-09: passing a fixed now must produce a deterministic occurred_at and last_event_at."""
    from app.db.models import Order
    from app.domain.order_state import OrderStatus
    from app.services.config import Settings
    from app.services.notification import InMemorySender, NotificationService
    from app.services.order_service import OrderService
    from app.services.pricing_service import PricingService

    customer = seeded["customer"]
    order = Order(
        customer_id=customer.id,  # type: ignore[attr-defined]
        idempotency_key="fixed-now-1",
        status=OrderStatus.PENDING_PAYMENT,
        currency="USD",
        subtotal=Decimal("10.00"),
        total=Decimal("10.00"),
    )
    db.add(order)
    db.flush()

    fixed = datetime(2030, 1, 1, tzinfo=UTC)
    service = OrderService(db, PricingService(), NotificationService(InMemorySender(), Settings()))
    service._record_event(order, None, OrderStatus.PAID, now=fixed)

    assert order.last_event_at == fixed


def test_order_lookup_branch_is_shared() -> None:
    """DS-20: get_order and list_order_events must not duplicate the admin/customer branch."""
    tree = ast.parse(ROUTER.read_text())
    funcs = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

    def called_names(fn: ast.FunctionDef) -> set[str]:
        return {
            call.func.id
            for call in ast.walk(fn)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }

    shared_helpers = called_names(funcs["get_order"]) & called_names(funcs["list_order_events"])
    private_helpers = {name for name in shared_helpers if name.startswith("_")}
    assert private_helpers, "get_order and list_order_events share no private helper"
