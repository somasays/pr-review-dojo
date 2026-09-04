from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.db.models import Order, OrderItem
from app.db.repositories import CustomerRepository, NotFound, OrderRepository, ProductRepository
from app.domain.order_state import OrderStatus


def _order(customer_id: int, key: str, status: str = "paid", total: str = "10.00") -> Order:
    return Order(
        customer_id=customer_id,
        idempotency_key=key,
        status=status,
        total=Decimal(total),
        subtotal=Decimal(total),
    )


def test_customer_lookups(db, seeded):
    repo = CustomerRepository(db)
    c = seeded["customer"]
    assert repo.get(c.id).email == "ada@example.com"
    assert repo.by_email("ada@example.com").id == c.id
    assert repo.by_email("nobody@example.com") is None
    with pytest.raises(NotFound):
        repo.get(999)


def test_products_by_skus(db, seeded):
    found = ProductRepository(db).by_skus(["WIDGET", "NOPE"])
    assert set(found) == {"WIDGET"}
    assert ProductRepository(db).by_skus([]) == {}


def test_order_add_and_scoped_get(db, seeded):
    repo = OrderRepository(db)
    c = seeded["customer"]
    p = seeded["products"]["WIDGET"]
    order = repo.add(
        _order(c.id, "key-00000001"),
        [OrderItem(product_id=p.id, sku=p.sku, quantity=2, unit_price=p.unit_price)],
    )
    db.commit()
    assert repo.get_for_customer(order.id, c.id).items[0].quantity == 2
    with pytest.raises(NotFound):
        repo.get_for_customer(order.id, c.id + 1)
    assert repo.by_idempotency_key(c.id, "key-00000001").id == order.id


def test_list_for_customer_is_newest_first_and_paged(db, seeded):
    repo = OrderRepository(db)
    c = seeded["customer"]
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(5):
        o = _order(c.id, f"key-{i:08d}")
        o.created_at = base + timedelta(days=i)
        db.add(o)
    db.commit()
    page = repo.list_for_customer(c.id, limit=2, offset=1)
    assert [o.idempotency_key for o in page] == ["key-00000003", "key-00000002"]


def test_count_by_status_and_created_between(db, seeded):
    repo = OrderRepository(db)
    c = seeded["customer"]
    base = datetime(2026, 5, 1, tzinfo=UTC)
    for i, status in enumerate(["paid", "paid", "cancelled"]):
        o = _order(c.id, f"key-{i:08d}", status=status)
        o.created_at = base + timedelta(days=i)
        db.add(o)
    db.commit()
    assert repo.count_by_status() == {"paid": 2, "cancelled": 1}
    rows = repo.created_between(base, base + timedelta(days=2))
    assert len(rows) == 2
    assert [o.status for o in repo.list_by_status(OrderStatus.CANCELLED)] == ["cancelled"]
