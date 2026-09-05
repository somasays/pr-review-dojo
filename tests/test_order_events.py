from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.models import Order
from app.db.repositories import OrderEventRepository
from app.domain.order_state import OrderStatus
from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import OrderService
from app.services.pricing_service import PricingService
from conftest import ADMIN_KEY, CUSTOMER_KEY


def _service(db: Session) -> OrderService:
    return OrderService(db, PricingService(), NotificationService(InMemorySender(), Settings()))


def _order(db: Session, customer_id: int, status: OrderStatus) -> Order:
    order = Order(
        customer_id=customer_id,
        idempotency_key=f"key-{status.value}",
        status=status,
        currency="USD",
        subtotal=Decimal("10.00"),
        total=Decimal("10.00"),
    )
    db.add(order)
    db.flush()
    return order


def test_paying_an_order_appends_an_event(db: Session, seeded: dict[str, object]) -> None:
    customer = seeded["customer"]
    order = _order(db, customer.id, OrderStatus.PENDING_PAYMENT)  # type: ignore[attr-defined]

    _service(db).mark_paid(order.id)

    events = OrderEventRepository(db).list_for_order(order.id)
    assert [(e.from_status, e.to_status) for e in events] == [
        (OrderStatus.PENDING_PAYMENT.value, OrderStatus.PAID)
    ]
    assert order.last_event_at is not None


def test_repeated_transition_is_not_recorded_twice(db: Session, seeded: dict[str, object]) -> None:
    customer = seeded["customer"]
    order = _order(db, customer.id, OrderStatus.PENDING_PAYMENT)  # type: ignore[attr-defined]
    service = _service(db)

    service.mark_paid(order.id)
    service.mark_paid(order.id)

    assert len(OrderEventRepository(db).list_for_order(order.id)) == 1


def test_history_endpoint_returns_events_oldest_first(client, seeded) -> None:
    body = {
        "idempotency_key": "events-endpoint-1",
        "items": [{"sku": "WIDGET", "quantity": 1}],
        "discount_codes": [],
    }
    created = client.post("/orders", json=body, headers={"X-API-Key": CUSTOMER_KEY})
    assert created.status_code == 201
    order_id = created.json()["id"]

    client.post(f"/orders/{order_id}/pay", headers={"X-API-Key": "admin-test-key"})
    client.post(f"/orders/{order_id}/ship", headers={"X-API-Key": "admin-test-key"})

    resp = client.get(f"/orders/{order_id}/events", headers={"X-API-Key": CUSTOMER_KEY})
    assert resp.status_code == 200
    assert [e["to_status"] for e in resp.json()] == ["pending_payment", "paid", "shipped"]
    assert all(e["actor"] == "service" for e in resp.json())


def test_recent_events_report_filters_by_status(client, seeded) -> None:
    body = {
        "idempotency_key": "recent-events-1",
        "items": [{"sku": "WIDGET", "quantity": 1}],
        "discount_codes": [],
    }
    created = client.post("/orders", json=body, headers={"X-API-Key": CUSTOMER_KEY})
    order_id = created.json()["id"]
    client.post(f"/orders/{order_id}/pay", headers={"X-API-Key": ADMIN_KEY})

    resp = client.get("/orders/reports/recent-events?status=paid", headers={"X-API-Key": ADMIN_KEY})
    assert resp.status_code == 200
    assert any(e["to_status"] == "paid" for e in resp.json())
    assert all(e["to_status"] == "paid" for e in resp.json())
