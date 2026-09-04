import pytest

from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import CreateOrderCommand, OrderService
from app.services.payment import InMemoryGateway
from app.services.pricing_service import ItemRequest, PricingService


@pytest.fixture
def sender() -> InMemorySender:
    return InMemorySender()


@pytest.fixture
def gateway() -> InMemoryGateway:
    return InMemoryGateway()


@pytest.fixture
def service(db, sender, gateway) -> OrderService:
    return OrderService(db, PricingService(), NotificationService(sender, Settings()), gateway)


def _cmd(customer_id: int, key: str = "key-00000010"):
    return CreateOrderCommand(
        customer_id=customer_id,
        idempotency_key=key,
        items=[ItemRequest("WIDGET", 2), ItemRequest("GADGET", 1)],
        discount_codes=[],
    )


def test_fulfill_charges_and_ships(db, seeded, service, sender, gateway):
    order = service.create(_cmd(seeded["customer"].id))
    db.commit()

    service.fulfill(order.id, "TRACK-1")

    assert order.status == "shipped"
    assert list(gateway.charges) == [f"order:{order.id}"]
    assert [m.dedupe_key for m in sender.sent] == [
        f"order-confirmed:{order.id}",
        f"order-shipped:{order.id}",
    ]
    assert "TRACK-1" in sender.sent[-1].body


def test_fulfill_is_a_no_op_for_a_shipped_order(db, seeded, service, sender):
    order = service.create(_cmd(seeded["customer"].id))
    db.commit()

    service.fulfill(order.id, "TRACK-2")
    service.fulfill(order.id, "TRACK-2")

    assert [m.dedupe_key for m in sender.sent] == [
        f"order-confirmed:{order.id}",
        f"order-shipped:{order.id}",
    ]


def test_fulfill_batch_reports_the_shipped_orders_to_the_warehouse(db, seeded, service, sender):
    customer_id = seeded["customer"].id
    first = service.create(_cmd(customer_id, key="key-00000011"))
    second = service.create(_cmd(customer_id, key="key-00000012"))
    db.commit()

    shipped = service.fulfill_batch([(first.id, "TRACK-3"), (second.id, "TRACK-4")])

    assert [o.id for o in shipped] == [first.id, second.id]
    digest = [m.dedupe_key for m in sender.sent if m.dedupe_key.startswith("warehouse-digest:")]
    assert digest == [f"warehouse-digest:{first.id}", f"warehouse-digest:{second.id}"]
