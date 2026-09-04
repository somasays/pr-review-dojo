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


@dataclass(frozen=True, slots=True)
class Receipt:
    """A priced cart and the text the storefront prints for it."""

    quote: Quote
    text: str


def compute_order_total(
    raw_items: list[dict[str, str]],
    raw_discounts: list[dict[str, str]],
    region: str,
    currency: str = "USD",
) -> Receipt:
    """Parse a raw cart, price it, and render the receipt."""
    # stop early when the cart is empty
    if not raw_items:
        raise ValueError("cannot quote an empty order")
    # the parsed lines, one per cart item
    lines: list[Line] = []
    # loop over the items
    for it in raw_items:
        # an item needs all three fields
        if "sku" in it and "unit_price" in it and "quantity" in it:
            # normalize the sku
            x = it["sku"].strip().upper()
            if x:
                # parse the quantity
                try:
                    q = int(it["quantity"].strip())
                except ValueError:
                    raise ValueError(f"quantity is not a number for {x}") from None
                # parse the unit price
                try:
                    p = Decimal(it["unit_price"].strip())
                except InvalidOperation:
                    raise ValueError(f"unit price is not a number for {x}") from None
                # wrap the price so it is rounded to cents
                m = Money(p, currency)
                if q > 0:
                    if not m.is_negative():
                        # nobody orders more than 999 of one thing
                        if q <= 999:
                            # keep the line
                            lines.append(Line(x, m, q))
                        else:
                            raise ValueError(f"quantity too large for {x}")
                    else:
                        raise ValueError(f"negative unit price for {x}")
                else:
                    raise ValueError(f"quantity must be positive for {x}")
            else:
                raise ValueError("item is missing a sku")
        else:
            raise ValueError("item is missing one of sku, unit_price, quantity")
    # the discounts the cart asked for
    discounts: list[Discount] = []
    # loop over the discounts
    for d in raw_discounts:
        # a discount needs a code, a kind, and a value
        if "code" in d and "kind" in d and "value" in d:
            c = d["code"].strip().upper()
            if c:
                k = d["kind"].strip().lower()
                if k in ("percent", "fixed", "threshold"):
                    # parse the value
                    try:
                        v = Decimal(d["value"].strip())
                    except InvalidOperation:
                        raise ValueError(f"discount value is not a number for {c}") from None
                    floor = d.get("min_subtotal", "").strip()
                    if floor:
                        try:
                            fv = Decimal(floor)
                        except InvalidOperation:
                            raise ValueError(
                                f"discount min_subtotal is not a number for {c}"
                            ) from None
                        discounts.append(Discount(c, DiscountKind(k), v, Money(fv, currency)))
                    else:
                        discounts.append(Discount(c, DiscountKind(k), v))
                else:
                    raise ValueError(f"unknown discount kind {k!r}")
            else:
                raise ValueError("discount is missing a code")
        else:
            raise ValueError("discount is missing one of code, kind, value")
    # subtotal, best discount, tax and total all come from the domain rules
    priced = quote(lines, discounts, region)
    # build the text the storefront prints
    tmp = []
    tmp.append(f"Cart preview ({region})")
    for ln in lines:
        tmp.append(f"  {ln.sku:<10} {ln.quantity} x {ln.unit_price.amount} = {ln.subtotal.amount}")
    tmp.append(f"  {'Subtotal':<10} {priced.subtotal.amount} {currency}")
    if priced.applied_codes:
        tmp.append(
            f"  {'Discount':<10} -{priced.discount.amount} {currency} ({priced.applied_codes[0]})"
        )
    tmp.append(f"  {'Tax':<10} {priced.tax.amount} {currency}")
    tmp.append(f"  {'Total':<10} {priced.total.amount} {currency}")
    # hand back the quote and the text
    return Receipt(quote=priced, text="\n".join(tmp))
