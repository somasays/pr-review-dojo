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
    # the parsed lines, one per cart item
    lines: list[Line] = []
    # loop over the items
    for it in raw_items:
        # an item needs all three fields
        if not all(field in it for field in _ITEM_FIELDS):
            raise ValueError("item is missing one of sku, unit_price, quantity")
        # normalize the sku
        x = it["sku"].strip().upper()
        if not x:
            raise ValueError("item is missing a sku")
        # parse the quantity
        try:
            q = int(it["quantity"].strip())
        except ValueError:
            raise ValueError(f"quantity is not a number for {x}") from None
        # parse the unit price
        p = _to_decimal(it["unit_price"], f"unit price is not a number for {x}")
        # Line rejects a non-positive quantity and a negative price for us,
        # with the same two messages, in the same order.
        ln = Line(x, Money(p, currency), q)
        if q > MAX_QUANTITY:
            raise ValueError(f"quantity too large for {x}")
        # keep the line
        lines.append(ln)
    return lines


def parse_discounts(raw_discounts: list[dict[str, str]], currency: str = "USD") -> list[Discount]:
    """Turn the raw discount payload into rules. Raises ValueError on bad input."""
    # the discounts the cart asked for
    discounts: list[Discount] = []
    # loop over the discounts
    for d in raw_discounts:
        # a discount needs a code, a kind, and a value
        if not all(field in d for field in _DISCOUNT_FIELDS):
            raise ValueError("discount is missing one of code, kind, value")
        c = d["code"].strip().upper()
        if not c:
            raise ValueError("discount is missing a code")
        k = d["kind"].strip().lower()
        if k not in _KINDS:
            raise ValueError(f"unknown discount kind {k!r}")
        # parse the value
        v = _to_decimal(d["value"], f"discount value is not a number for {c}")
        floor = d.get("min_subtotal", "").strip()
        fv = (
            Money(_to_decimal(floor, f"discount min_subtotal is not a number for {c}"), currency)
            if floor
            else None
        )
        discounts.append(Discount(c, DiscountKind(k), v, fv))
    return discounts


def format_receipt(lines: list[Line], priced: Quote, region: str) -> str:
    """Render the receipt text for a priced cart."""
    currency = priced.subtotal.currency
    # build the text the storefront prints
    tmp = [f"Cart preview ({region})"]
    for ln in lines:
        tmp.append(
            f"  {ln.sku:<{LABEL_WIDTH}} {ln.quantity} x "
            f"{ln.unit_price.amount} = {ln.subtotal.amount}"
        )
    tmp.append(f"  {'Subtotal':<{LABEL_WIDTH}} {priced.subtotal.amount} {currency}")
    if priced.applied_codes:
        tmp.append(
            f"  {'Discount':<{LABEL_WIDTH}} -{priced.discount.amount} "
            f"{currency} ({priced.applied_codes[0]})"
        )
    tmp.append(f"  {'Tax':<{LABEL_WIDTH}} {priced.tax.amount} {currency}")
    tmp.append(f"  {'Total':<{LABEL_WIDTH}} {priced.total.amount} {currency}")
    return "\n".join(tmp)


def compute_order_total(
    raw_items: list[dict[str, str]],
    raw_discounts: list[dict[str, str]],
    region: str,
    currency: str = "USD",
) -> Receipt:
    """Parse a raw cart, price it, and render the receipt."""
    lines = parse_items(raw_items, currency)
    discounts = parse_discounts(raw_discounts, currency)
    # subtotal, best discount, tax and total all come from the domain rules
    priced = quote(lines, discounts, region)
    return Receipt(quote=priced, text=format_receipt(lines, priced, region))
