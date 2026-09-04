"""Hidden tests for exercise 02: tiered volume discounts."""

import ast
import inspect
from datetime import date
from decimal import Decimal

from app.domain.money import Money
from app.domain.pricing import (
    Discount,
    DiscountKind,
    Line,
    is_holiday_bonus_window,
    quote,
    tier_for,
    volume_discount,
    volume_discount_with_holiday_bonus,
    volume_receipt_shares,
)

FLAT5 = Discount("FLAT5", DiscountKind.FIXED, Decimal("5"))


def test_tier_boundary_is_inclusive():
    assert tier_for(9) is None
    first = tier_for(10)
    assert first is not None
    assert first.percent_off == Decimal("5")
    second = tier_for(50)
    assert second is not None
    assert second.percent_off == Decimal("12")
    assert tier_for(49).percent_off == Decimal("5")  # type: ignore[union-attr]


def test_quote_at_the_advertised_quantity_gets_the_tier():
    q = quote([Line("PEN", Money.of("1.00"), 10)], [], "US-OR")
    assert q.discount == Money.of("0.50")
    assert q.applied_codes == ("VOLUME10",)


def test_stacked_tier_and_code_never_exceed_the_subtotal():
    lines = [Line("PEN", Money.of("0.40"), 12)]
    q = quote(lines, [FLAT5], "US-OR")
    assert q.subtotal == Money.of("4.80")
    assert q.discount == Money.of("4.80")
    assert q.total == Money.zero()
    assert not q.total.is_negative()
    assert not q.taxable.is_negative()


def test_tier_codes_do_not_leak_between_quotes():
    first = quote([Line("PEN", Money.of("1.00"), 60)], [], "US-OR")
    assert first.applied_codes == ("VOLUME50",)
    second = quote([Line("PEN", Money.of("1.00"), 2)], [], "US-OR")
    assert second.applied_codes == ()
    assert second.discount.is_zero()


def test_volume_receipt_shares_reuses_allocate():
    src = inspect.getsource(volume_receipt_shares)
    tree = ast.parse(src)
    called_names = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "allocate" in called_names


def test_holiday_bonus_window_takes_a_fixed_date():
    sig = inspect.signature(is_holiday_bonus_window)
    assert "today" in sig.parameters
    assert is_holiday_bonus_window(date(2026, 12, 1)) is True
    assert is_holiday_bonus_window(date(2026, 6, 1)) is False


def test_holiday_bonus_is_a_separate_function_not_a_flag():
    for fn in (volume_discount, volume_discount_with_holiday_bonus):
        for param in inspect.signature(fn).parameters.values():
            assert param.annotation is not bool
