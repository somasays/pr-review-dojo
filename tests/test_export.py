from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.models import Order, OrderItem
from app.services.export_service import ExportFilters, build_export
from conftest import CUSTOMER_KEY

H = {"X-API-Key": CUSTOMER_KEY}


def _paid_order(db: Session, customer_id: int, product_id: int, key: str) -> Order:
    order = Order(
        customer_id=customer_id,
        idempotency_key=key,
        status="paid",
        subtotal=Decimal("19.99"),
        total=Decimal("21.44"),
        created_at=datetime.now(tz=UTC),
    )
    order.items = [
        OrderItem(product_id=product_id, sku="WIDGET", quantity=1, unit_price=Decimal("19.99"))
    ]
    db.add(order)
    db.commit()
    return order


def test_export_page_returns_matching_orders(db, seeded):
    customer, widget = seeded["customer"], seeded["products"]["WIDGET"]
    _paid_order(db, customer.id, widget.id, "export-0001")
    _paid_order(db, customer.id, widget.id, "export-0002")

    now = datetime.now(tz=UTC)
    filters = ExportFilters(
        statuses=["paid"], start=now - timedelta(days=7), end=now + timedelta(minutes=1), limit=50
    )

    page = build_export(db, customer.id, filters)

    assert len(page.rows) == 2
    assert page.orders == 2
    assert page.gross == Decimal("42.88")
    assert page.rows[0].products == "Widget"
    assert page.next_cursor is None


def test_export_endpoint_is_scoped_to_the_caller(client):
    body = {"idempotency_key": "key-00000023", "items": [{"sku": "WIDGET", "quantity": 1}]}
    client.post("/orders", json=body, headers=H)

    r = client.get("/reports/orders/export?status=pending_payment&days=1", headers=H)

    assert r.status_code == 200
    assert r.json()["orders"] == 1
    assert r.json()["items"][0]["products"] == "Widget"
