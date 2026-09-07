"""Tests for the flash sale pricing rule and its checkout integration."""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from app.db.models import Product
from app.domain.flash_sale import FlashSale
from app.domain.money import Money
from app.services.config import Settings
from app.services.flash_sales import SaleCounter
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import CreateOrderCommand, OrderService
from app.services.pricing_service import ItemRequest, PricingService

SALE = FlashSale(
    sku="FLASHDEAL",
    starts_at=datetime(2020, 1, 1, tzinfo=UTC),
    ends_at=datetime(2030, 1, 1, tzinfo=UTC),
    percent_off=Decimal("30"),
    floor_price=Money.of("5.00"),
    per_customer_unit_cap=3,
)


def test_sale_price_applies_percent_off() -> None:
    assert SALE.sale_price(Money.of("20.00")) == Money.of("14.00")


def test_sale_price_never_drops_below_floor() -> None:
    deep = replace(SALE, percent_off=Decimal("90"))
    assert deep.sale_price(Money.of("20.00")) == Money.of("5.00")


def test_units_within_cap_allows_and_blocks() -> None:
    assert SALE.units_within_cap(already_purchased=0, quantity=1) is True
    assert SALE.units_within_cap(already_purchased=4, quantity=2) is False


def test_checkout_applies_the_sale_price(db, seeded) -> None:
    product = Product(sku="FLASHDEAL", name="Flash Deal", unit_price=Decimal("20.00"), stock=100)
    db.add(product)
    db.commit()

    sender = InMemorySender()
    service = OrderService(
        db, PricingService(), NotificationService(sender, Settings()), SaleCounter()
    )
    cmd = CreateOrderCommand(
        customer_id=seeded["customer"].id,
        idempotency_key="flash-key-1",
        items=[ItemRequest("FLASHDEAL", 1)],
        discount_codes=[],
    )
    order = service.create(cmd)
    db.commit()

    assert order.items[0].unit_price == Decimal("14.00")
    assert order.subtotal == Decimal("14.00")


def test_sale_counter_stops_its_background_thread() -> None:
    before = threading.active_count()
    counter = SaleCounter(sweep_interval_seconds=30)
    counter.start()
    counter.stop()  # joins the thread, no sleep needed to know it is done
    assert threading.active_count() == before
