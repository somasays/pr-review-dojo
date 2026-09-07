"""Hidden tests for exercise 35: flash sale windows."""

from __future__ import annotations

import inspect
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import app.api.deps as deps_module
from app.db.models import Product
from app.domain.flash_sale import FlashSale
from app.domain.money import Money
from app.services.config import Settings
from app.services.flash_sales import SaleCapExceeded, SaleCounter
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import CreateOrderCommand, OrderService
from app.services.pricing_service import ItemRequest, PricingService


def _sale(**overrides: object) -> FlashSale:
    fields: dict[str, object] = {
        "sku": "FLASHDEAL",
        "starts_at": datetime(2020, 1, 1, tzinfo=UTC),
        "ends_at": datetime(2030, 1, 1, tzinfo=UTC),
        "percent_off": Decimal("30"),
        "floor_price": Money.of("5.00"),
        "per_customer_unit_cap": 3,
    }
    fields.update(overrides)
    return FlashSale(**fields)  # type: ignore[arg-type]


# --- Major: LG-12 style boundary on the per-customer cap ---------------------


def test_units_within_cap_includes_the_cap_itself() -> None:
    sale = _sale(per_customer_unit_cap=3)
    # 1 already bought + 2 more lands exactly on the cap; that purchase
    # should be allowed, not rejected.
    assert sale.units_within_cap(already_purchased=1, quantity=2) is True
    assert sale.units_within_cap(already_purchased=1, quantity=3) is False


# --- Blocker: SV-04 style swallowed exception at checkout --------------------


def test_checkout_enforces_the_per_customer_cap(db, seeded) -> None:
    product = Product(sku="FLASHDEAL", name="Flash Deal", unit_price=Decimal("20.00"), stock=100)
    db.add(product)
    db.commit()

    counter = SaleCounter()
    counter.record_purchase("FLASHDEAL", seeded["customer"].id, 3)  # already at the cap
    service = OrderService(
        db, PricingService(), NotificationService(InMemorySender(), Settings()), counter
    )
    cmd = CreateOrderCommand(
        customer_id=seeded["customer"].id,
        idempotency_key="flash-key-cap",
        items=[ItemRequest("FLASHDEAL", 1)],
        discount_codes=[],
    )
    with pytest.raises(SaleCapExceeded):
        service.create(cmd)


# --- Major: CC-06 style unlocked read-modify-write on the counter ------------


def test_record_purchase_does_not_lose_increments_under_concurrency() -> None:
    counter = SaleCounter()
    workers = 8
    rounds = 50
    barrier = threading.Barrier(workers)

    def worker() -> None:
        for _ in range(rounds):
            barrier.wait()
            counter.record_purchase("FLASHDEAL", 1, 1)

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert not any(t.is_alive() for t in threads)

    assert counter.units_sold("FLASHDEAL") == workers * rounds
    assert counter.units_sold_by_customer("FLASHDEAL", 1) == workers * rounds


# --- Blocker: CC-04 style unsynchronized lazy singleton -----------------------


def test_get_sale_counter_is_a_true_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps_module, "_sale_counter", None)

    real_init = SaleCounter.__init__

    def slow_init(self: SaleCounter, *args: object, **kwargs: object) -> None:
        threading.Event().wait(0.01)
        real_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(SaleCounter, "__init__", slow_init)

    barrier = threading.Barrier(8)
    results: list[SaleCounter | None] = [None] * 8

    def call(i: int) -> None:
        barrier.wait()
        results[i] = deps_module.get_sale_counter()

    threads = [threading.Thread(target=call, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert all(r is results[0] for r in results)
    closers = [t for t in threading.enumerate() if t.name.startswith("sale-closer")]
    assert len(closers) == 1
    assert results[0] is not None
    results[0].stop()


# --- Refactor: DS-08 style duplicate window logic in the closer thread -------


def test_closer_thread_reuses_is_active() -> None:
    text = Path("app/services/flash_sales.py").read_text()
    assert "sale.is_active(now)" in text
    assert "> sale.ends_at" not in text


# --- Design: DS-09 style clock hidden inside the checkout step ---------------


def test_flash_sale_prices_takes_now_as_a_parameter() -> None:
    sig = inspect.signature(OrderService._flash_sale_prices)
    assert "now" in sig.parameters


# --- Design: DS-21 style formatting split out of the admin endpoint ----------


def test_active_sale_formatting_is_a_pure_function() -> None:
    from app.api.routers.reports import format_active_sale

    sale = _sale()
    row = format_active_sale("FLASHDEAL", sale, 7)
    assert row.units_sold == 7
    assert row.sku == "FLASHDEAL"


# --- Test: TR-10 style sleep in the shipped concurrency test -----------------


def test_shipped_test_does_not_sleep_to_synchronize() -> None:
    text = Path("tests/test_flash_sale.py").read_text()
    assert "time.sleep(" not in text
