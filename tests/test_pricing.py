from decimal import Decimal

import pytest

from app.domain.money import Money
from app.domain.pricing import (
    Discount,
    DiscountKind,
    Line,
    VolumeTier,
    best_discount,
    quote,
    tax_rate_for,
    tier_for,
    unit_price_after_discount,
    volume_discount,
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


def test_volume_tier_lookup():
    small = tier_for(12)
    assert small is not None
    assert small.percent_off == Decimal("5")
    assert small.code == "VOLUME10"
    big = tier_for(60)
    assert big is not None
    assert big.percent_off == Decimal("12")
    assert tier_for(3) is None


def test_volume_tier_boundary_is_inclusive():
    assert tier_for(9) is None
    assert tier_for(10) is not None
    assert tier_for(10).percent_off == Decimal("5")  # type: ignore[union-attr]
    assert tier_for(49).percent_off == Decimal("5")  # type: ignore[union-attr]
    assert tier_for(50) is not None
    assert tier_for(50).percent_off == Decimal("12")  # type: ignore[union-attr]


def test_volume_discount_amounts():
    assert volume_discount(Money.of("100.00"), 60).amount == Decimal("12.00")
    assert volume_discount(Money.of("100.00"), 12).amount == Decimal("5.00")
    assert volume_discount(Money.of("100.00"), 3).is_zero()


def test_volume_discount_floors_fractional_cents():
    # 5 percent of 19.99 is 0.9995, which must not round up to a full cent.
    assert volume_discount(Money.of("19.99"), 12).amount == Decimal("0.99")


def test_quote_applies_volume_tier():
    q = quote([Line("PEN", Money.of("2.00"), 20)], [], "US-OR")
    assert q.subtotal.amount == Decimal("40.00")
    assert q.discount.amount == Decimal("2.00")
    assert q.total.amount == Decimal("38.00")
    assert q.applied_codes == ("VOLUME10",)


def test_quote_below_first_tier_is_unchanged():
    q = quote([Line("PEN", Money.of("2.00"), 4)], [], "US-OR")
    assert q.discount.is_zero()
    assert q.total.amount == Decimal("8.00")


def test_tier_rejects_percent_over_100():
    with pytest.raises(ValueError):
        VolumeTier(10, Decimal("120"))
    with pytest.raises(ValueError):
        VolumeTier(0, Decimal("5"))
