"""Builders shared by the hidden tests."""

from __future__ import annotations

from decimal import Decimal

import httpx

from app.db.models import Order, OrderItem


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://payments.test")


def approve(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(201, json={"id": "ch_123", "status": "captured"})


def make_order(db, seeded, status: str = "pending_payment", key: str = "pay-00000001") -> Order:
    row = Order(
        customer_id=seeded["customer"].id,
        idempotency_key=key,
        status=status,
        currency="USD",
        subtotal=Decimal("39.98"),
        discount=Decimal("0.00"),
        tax=Decimal("3.60"),
        total=Decimal("43.58"),
    )
    row.items = [
        OrderItem(
            product_id=seeded["products"]["WIDGET"].id,
            sku="WIDGET",
            quantity=2,
            unit_price=Decimal("19.99"),
        )
    ]
    db.add(row)
    db.commit()
    return row
