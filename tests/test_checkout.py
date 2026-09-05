from decimal import Decimal

import pytest

from app.domain.checkout import compute_order_total

CART = [
    {"sku": "widget", "unit_price": "19.99", "quantity": "2"},
    {"sku": "GADGET", "unit_price": "120.00", "quantity": "1"},
]


def test_totals_without_a_discount():
    receipt = compute_order_total(CART, [], "US-CA")
    assert receipt.quote.subtotal.amount == Decimal("159.98")
    assert receipt.quote.discount.is_zero()
    assert receipt.quote.tax.amount == Decimal("11.60")
    assert receipt.quote.total.amount == Decimal("171.58")


def test_percent_discount_is_applied_before_tax():
    receipt = compute_order_total(
        CART, [{"code": "welcome10", "kind": "percent", "value": "10"}], "US-OR"
    )
    assert receipt.quote.discount.amount == Decimal("16.00")
    assert receipt.quote.applied_codes == ("WELCOME10",)
    assert receipt.quote.total.amount == Decimal("143.98")


def test_receipt_text_lists_every_line():
    receipt = compute_order_total(CART, [], "US-OR")
    assert receipt.text.splitlines()[0] == "Cart preview (US-OR)"
    assert "WIDGET" in receipt.text
    assert "GADGET" in receipt.text
    assert receipt.text.splitlines()[-1].endswith("159.98 USD")


def test_empty_cart_is_rejected():
    with pytest.raises(ValueError, match="cannot quote an empty order"):
        compute_order_total([], [], "US-CA")
