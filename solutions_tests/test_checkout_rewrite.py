"""Hidden tests for exercise 12.

The characterization tests below pin the behavior of `compute_order_total` and
pass on both the exercise branch and the rewrite: a rewrite that changes any
total, any message, or any line of the receipt is wrong. The structural tests
at the bottom are the ones that fail before the rewrite.
"""

from __future__ import annotations

import ast
import pathlib
from decimal import Decimal

import pytest

from app.domain.checkout import compute_order_total

CHECKOUT_SOURCE = pathlib.Path("app/domain/checkout.py")

CART = [
    {"sku": " widget ", "unit_price": "19.99", "quantity": "2"},
    {"sku": "GADGET", "unit_price": "120.00", "quantity": " 1 "},
]

RECEIPT_NO_DISCOUNT = [
    "Cart preview (US-CA)",
    "  WIDGET     2 x 19.99 = 39.98",
    "  GADGET     1 x 120.00 = 120.00",
    "  Subtotal   159.98 USD",
    "  Tax        11.60 USD",
    "  Total      171.58 USD",
]


def test_subtotal_tax_and_total_without_a_discount():
    receipt = compute_order_total(CART, [], "US-CA")
    assert receipt.quote.subtotal.amount == Decimal("159.98")
    assert receipt.quote.discount.amount == Decimal("0.00")
    assert receipt.quote.tax.amount == Decimal("11.60")
    assert receipt.quote.total.amount == Decimal("171.58")
    assert receipt.quote.applied_codes == ()


def test_receipt_text_is_unchanged():
    assert compute_order_total(CART, [], "US-CA").text.splitlines() == RECEIPT_NO_DISCOUNT


def test_receipt_text_names_the_applied_code():
    receipt = compute_order_total(
        CART, [{"code": " w10 ", "kind": "PERCENT", "value": "10"}], "US-CA"
    )
    assert receipt.text.splitlines()[4] == "  Discount   -16.00 USD (W10)"
    assert receipt.quote.tax.amount == Decimal("10.44")
    assert receipt.quote.total.amount == Decimal("154.42")


def test_best_discount_wins_and_ties_go_to_the_first_listed():
    codes = [
        {"code": "FIRST", "kind": "percent", "value": "10"},
        {"code": "SECOND", "kind": "percent", "value": "10"},
        {"code": "SMALL", "kind": "fixed", "value": "5"},
    ]
    receipt = compute_order_total(CART, codes, "US-OR")
    assert receipt.quote.discount.amount == Decimal("16.00")
    assert receipt.quote.applied_codes == ("FIRST",)


def test_fixed_discount_never_exceeds_the_subtotal():
    receipt = compute_order_total(CART, [{"code": "HUGE", "kind": "fixed", "value": "500"}], "GB")
    assert receipt.quote.discount.amount == Decimal("159.98")
    assert receipt.quote.tax.amount == Decimal("0.00")
    assert receipt.quote.total.amount == Decimal("0.00")


def test_threshold_discount_below_the_minimum_is_not_applied():
    codes = [
        {"code": "BULK15", "kind": "threshold", "value": "15", "min_subtotal": "200"},
    ]
    receipt = compute_order_total(CART, codes, "US-OR")
    assert receipt.quote.discount.is_zero()
    assert receipt.quote.applied_codes == ()


def test_threshold_discount_at_the_minimum_is_applied():
    codes = [
        {"code": "BULK15", "kind": "threshold", "value": "15", "min_subtotal": "159.98"},
    ]
    receipt = compute_order_total(CART, codes, "US-OR")
    assert receipt.quote.discount.amount == Decimal("24.00")
    assert receipt.quote.total.amount == Decimal("135.98")


def test_unknown_region_is_taxed_at_zero():
    receipt = compute_order_total(CART, [], "MARS")
    assert receipt.quote.tax.is_zero()
    assert receipt.quote.total.amount == Decimal("159.98")


def test_every_region_rate_is_preserved():
    totals = {
        region: compute_order_total(CART, [], region).quote.tax.amount
        for region in ("US-CA", "US-NY", "US-TX", "US-OR", "GB", "DE")
    }
    assert totals == {
        "US-CA": Decimal("11.60"),
        "US-NY": Decimal("6.40"),
        "US-TX": Decimal("10.00"),
        "US-OR": Decimal("0.00"),
        "GB": Decimal("32.00"),
        "DE": Decimal("30.40"),
    }


@pytest.mark.parametrize(
    ("items", "message"),
    [
        ([{"sku": "A", "unit_price": "1.00"}], "item is missing one of sku, unit_price, quantity"),
        ([{"sku": "  ", "unit_price": "1.00", "quantity": "1"}], "item is missing a sku"),
        ([{"sku": "A", "unit_price": "1.00", "quantity": "two"}], "quantity is not a number for A"),
        ([{"sku": "A", "unit_price": "free", "quantity": "1"}], "unit price is not a number for A"),
        ([{"sku": "A", "unit_price": "1.00", "quantity": "0"}], "quantity must be positive for A"),
        ([{"sku": "A", "unit_price": "-1.00", "quantity": "1"}], "negative unit price for A"),
        ([{"sku": "A", "unit_price": "1.00", "quantity": "1000"}], "quantity too large for A"),
    ],
)
def test_item_errors_keep_their_messages(items, message):
    with pytest.raises(ValueError) as info:
        compute_order_total(items, [], "US-CA")
    assert str(info.value) == message


@pytest.mark.parametrize(
    ("codes", "message"),
    [
        ([{"code": "A", "kind": "percent"}], "discount is missing one of code, kind, value"),
        ([{"code": " ", "kind": "percent", "value": "10"}], "discount is missing a code"),
        ([{"code": "A", "kind": "half", "value": "10"}], "unknown discount kind 'half'"),
        (
            [{"code": "A", "kind": "percent", "value": "ten"}],
            "discount value is not a number for A",
        ),
        (
            [{"code": "A", "kind": "threshold", "value": "10"}],
            "threshold discount needs min_subtotal",
        ),
    ],
)
def test_discount_errors_keep_their_messages(codes, message):
    with pytest.raises(ValueError) as info:
        compute_order_total(CART, codes, "US-CA")
    assert str(info.value) == message


def test_empty_cart_is_rejected():
    with pytest.raises(ValueError) as info:
        compute_order_total([], [], "US-CA")
    assert str(info.value) == "cannot quote an empty order"


def test_parsing_and_formatting_are_callable_on_their_own():
    from app.domain.checkout import format_receipt, parse_discounts, parse_items
    from app.domain.pricing import quote as price_quote

    lines = parse_items(CART)
    assert [(line.sku, line.quantity) for line in lines] == [("WIDGET", 2), ("GADGET", 1)]
    discounts = parse_discounts([{"code": "w10", "kind": "percent", "value": "10"}])
    assert [d.code for d in discounts] == ["W10"]
    priced = price_quote(lines, discounts, "US-CA")
    assert format_receipt(lines, priced, "US-CA").splitlines()[0] == "Cart preview (US-CA)"


def _functions(tree: ast.Module) -> list[ast.FunctionDef]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]


def _depth(node: ast.AST, level: int = 0) -> int:
    nesting = (ast.For, ast.While, ast.If, ast.With, ast.Try)
    deepest = level
    for child in ast.iter_child_nodes(node):
        child_level = level + 1 if isinstance(child, nesting) else level
        deepest = max(deepest, _depth(child, child_level))
    return deepest


def test_no_function_is_longer_than_forty_lines():
    tree = ast.parse(CHECKOUT_SOURCE.read_text())
    longest = {
        fn.name: (fn.end_lineno or fn.lineno) - fn.lineno + 1
        for fn in _functions(tree)
        if (fn.end_lineno or fn.lineno) - fn.lineno + 1 > 40
    }
    assert longest == {}


def test_nesting_never_goes_deeper_than_three_levels():
    tree = ast.parse(CHECKOUT_SOURCE.read_text())
    too_deep = {fn.name: _depth(fn) for fn in _functions(tree) if _depth(fn) > 3}
    assert too_deep == {}
