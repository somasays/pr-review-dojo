import pytest

from app.db.repositories import DiscountCodeRepository
from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import CreateOrderCommand, OrderService
from app.services.pricing_service import (
    DiscountExhausted,
    ItemRequest,
    PricingService,
    UnknownDiscountCode,
)
from conftest import ADMIN_KEY

A = {"X-API-Key": ADMIN_KEY}


def test_codes_resolve_from_the_database(db, seeded):
    svc = PricingService(db)
    assert [d.code for d in svc.resolve_discounts([" flat5 "])] == ["FLAT5"]
    with pytest.raises(UnknownDiscountCode):
        svc.resolve_discounts(["NOPE"])


def test_code_at_its_limit_is_rejected(db, seeded):
    row = seeded["discounts"]["WELCOME10"]
    row.max_redemptions, row.times_redeemed = 1, 1
    db.commit()
    with pytest.raises(DiscountExhausted):
        PricingService(db).resolve_discounts(["WELCOME10"])


def test_create_records_a_redemption(db, seeded):
    notify = NotificationService(InMemorySender(), Settings())
    service = OrderService(db, PricingService(db), notify)
    cmd = CreateOrderCommand(
        customer_id=seeded["customer"].id,
        idempotency_key="key-00000042",
        items=[ItemRequest("WIDGET", 2)],
        discount_codes=["FLAT5"],
    )
    assert service.create(cmd).discount_code == "FLAT5"
    db.commit()
    assert DiscountCodeRepository(db).by_code("FLAT5").times_redeemed == 1


def test_admin_can_list_and_create_codes(client):
    listed = client.get("/discounts", headers=A)
    assert [d["code"] for d in listed.json()] == ["BULK15", "FLAT5", "WELCOME10"]
    created = client.post(
        "/discounts",
        json={"code": "spring20", "kind": "percent", "value": "20", "max_redemptions": 5},
        headers=A,
    )
    assert created.status_code == 201, created.text
    assert created.json()["code"] == "SPRING20"
    assert client.post("/discounts/flat5/deactivate", headers=A).json()["active"] is False
