from decimal import Decimal

import pytest

from app.domain.money import Money
from app.domain.pricing import (
    Discount,
    DiscountKind,
    Line,
    best_discount,
    quote,
    tax_rate_for,
    unit_price_after_discount,
)

WIDGET = Line("WIDGET", Money.of("19.99"), 2)
GADGET = Line("GADGET", Money.of("120.00"), 1)


def test_line_validation():
    with pytest.raises(ValueError):
        Line("X", Money.of("1"), 0)
    with pytest.raises(ValueError):
        Line("X", Money.of("-1"), 1)


def test_quote_no_discount_with_tax():
    q = quote([WIDGET, GADGET], [], "US-CA")
    assert q.subtotal.amount == Decimal("159.98")
    assert q.discount.is_zero()
    assert q.tax.amount == Decimal("11.60")
    assert q.total.amount == Decimal("171.58")
    assert q.applied_codes == ()


def test_percent_discount_applied_before_tax():
    ten = Discount("TEN", DiscountKind.PERCENT, Decimal("10"))
    q = quote([GADGET], [ten], "US-NY")
    assert q.discount.amount == Decimal("12.00")
    assert q.tax.amount == Decimal("4.32")
    assert q.total.amount == Decimal("112.32")
    assert q.applied_codes == ("TEN",)


def test_threshold_discount_only_above_minimum():
    bulk = Discount("BULK", DiscountKind.THRESHOLD, Decimal("15"), min_subtotal=Money.of("200"))
    assert bulk.apply(Money.of("199.99")).is_zero()
    assert bulk.apply(Money.of("200.00")).amount == Decimal("30.00")


def test_fixed_discount_never_exceeds_subtotal():
    five = Discount("FIVE", DiscountKind.FIXED, Decimal("5"))
    assert five.apply(Money.of("3.00")).amount == Decimal("3.00")


def test_best_discount_picks_largest_and_does_not_stack():
    ten = Discount("TEN", DiscountKind.PERCENT, Decimal("10"))
    five = Discount("FIVE", DiscountKind.FIXED, Decimal("5"))
    assert best_discount(Money.of("40"), [five, ten]) is five
    assert best_discount(Money.of("60"), [five, ten]) is ten
    assert best_discount(Money.of("50"), [five, ten]) is five  # tie goes to first


def test_unknown_region_has_no_tax():
    assert tax_rate_for("ZZ") == Decimal("0")
    assert quote([WIDGET], [], "ZZ").tax.is_zero()


def test_empty_order_rejected():
    with pytest.raises(ValueError):
        quote([], [], "US-CA")


def test_unit_price_after_discount():
    assert unit_price_after_discount(WIDGET, Money.of("1.00")).amount == Decimal("19.49")
