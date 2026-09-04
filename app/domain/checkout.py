"""Cart preview for the storefront.

The storefront posts a cart as plain strings, because the browser has no
Decimal and we refuse to accept floats for money. This module turns that raw
payload into a priced quote plus the receipt text the storefront prints, so
the front end never does arithmetic of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.domain.money import Money
from app.domain.pricing import Discount, DiscountKind, Line, Quote, quote

LABEL_WIDTH = 10
MAX_QUANTITY = 999

_ITEM_FIELDS = ("sku", "unit_price", "quantity")
_DISCOUNT_FIELDS = ("code", "kind", "value")
_KINDS = {kind.value for kind in DiscountKind}


@dataclass(frozen=True, slots=True)
class Receipt:
    """A priced cart and the text the storefront prints for it."""

    quote: Quote
    text: str


def _to_decimal(value: str, message: str) -> Decimal:
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        raise ValueError(message) from None


def parse_items(raw_items: list[dict[str, str]], currency: str = "USD") -> list[Line]:
    """Turn the raw cart payload into lines. Raises ValueError on bad input."""
    lines: list[Line] = []
    for raw in raw_items:
        if not all(field in raw for field in _ITEM_FIELDS):
            raise ValueError("item is missing one of sku, unit_price, quantity")
        sku = raw["sku"].strip().upper()
        if not sku:
            raise ValueError("item is missing a sku")
        try:
            quantity = int(raw["quantity"].strip())
        except ValueError:
            raise ValueError(f"quantity is not a number for {sku}") from None
        price = _to_decimal(raw["unit_price"], f"unit price is not a number for {sku}")
        # Line rejects a non-positive quantity and a negative price for us.
        line = Line(sku, Money(price, currency), quantity)
        if quantity > MAX_QUANTITY:
            raise ValueError(f"quantity too large for {sku}")
        lines.append(line)
    return lines


def parse_discounts(raw_discounts: list[dict[str, str]], currency: str = "USD") -> list[Discount]:
    """Turn the raw discount payload into rules. Raises ValueError on bad input."""
    discounts: list[Discount] = []
    for raw in raw_discounts:
        if not all(field in raw for field in _DISCOUNT_FIELDS):
            raise ValueError("discount is missing one of code, kind, value")
        code = raw["code"].strip().upper()
        if not code:
            raise ValueError("discount is missing a code")
        kind = raw["kind"].strip().lower()
        if kind not in _KINDS:
            raise ValueError(f"unknown discount kind {kind!r}")
        value = _to_decimal(raw["value"], f"discount value is not a number for {code}")
        floor = raw.get("min_subtotal", "").strip()
        minimum = (
            Money(_to_decimal(floor, f"discount min_subtotal is not a number for {code}"), currency)
            if floor
            else None
        )
        discounts.append(Discount(code, DiscountKind(kind), value, minimum))
    return discounts


def format_receipt(lines: list[Line], priced: Quote, region: str) -> str:
    """Render the receipt text for a priced cart."""
    currency = priced.subtotal.currency
    out = [f"Cart preview ({region})"]
    for line in lines:
        out.append(
            f"  {line.sku:<{LABEL_WIDTH}} {line.quantity} x "
            f"{line.unit_price.amount} = {line.subtotal.amount}"
        )
    out.append(f"  {'Subtotal':<{LABEL_WIDTH}} {priced.subtotal.amount} {currency}")
    if priced.applied_codes:
        out.append(
            f"  {'Discount':<{LABEL_WIDTH}} -{priced.discount.amount} "
            f"{currency} ({priced.applied_codes[0]})"
        )
    out.append(f"  {'Tax':<{LABEL_WIDTH}} {priced.tax.amount} {currency}")
    out.append(f"  {'Total':<{LABEL_WIDTH}} {priced.total.amount} {currency}")
    return "\n".join(out)


def compute_order_total(
    raw_items: list[dict[str, str]],
    raw_discounts: list[dict[str, str]],
    region: str,
    currency: str = "USD",
) -> Receipt:
    """Parse a raw cart, price it, and render the receipt."""
    lines = parse_items(raw_items, currency)
    discounts = parse_discounts(raw_discounts, currency)
    priced = quote(lines, discounts, region)
    return Receipt(quote=priced, text=format_receipt(lines, priced, region))
