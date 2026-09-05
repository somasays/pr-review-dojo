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
from app.domain.pricing import Quote


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
    # the parsed rows, one tuple per cart item
    rows: list[tuple[str, Money, int]] = []
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
                            # keep the row
                            rows.append((x, m, q))
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
    # add up the line totals
    sub = Money.zero(currency)
    for r in rows:
        sub = sub + r[1] * r[2]
    # the discount that takes off the most wins, first listed wins a tie
    disc = Money.zero(currency)
    code_used = ""
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
                    # work out what this one takes off
                    if k == "percent":
                        off = Money(sub.amount * v / Decimal(100), currency)
                    elif k == "fixed":
                        off = Money(v, currency)
                    else:
                        floor = d.get("min_subtotal", "").strip()
                        if not floor:
                            raise ValueError("threshold discount needs min_subtotal")
                        try:
                            fv = Decimal(floor)
                        except InvalidOperation:
                            raise ValueError(
                                f"discount min_subtotal is not a number for {c}"
                            ) from None
                        if Money(fv, currency) <= sub:
                            off = Money(sub.amount * v / Decimal(100), currency)
                        else:
                            off = Money.zero(currency)
                    # never take off more than the cart is worth
                    if sub < off:
                        off = sub
                    # keep the best one so far
                    if disc < off:
                        disc = off
                        code_used = c
                else:
                    raise ValueError(f"unknown discount kind {k!r}")
            else:
                raise ValueError("discount is missing a code")
        else:
            raise ValueError("discount is missing one of code, kind, value")
    # tax is charged on the discounted amount
    taxable = sub - disc
    # work out the rate for the region
    if region == "US-CA":
        t = Money(taxable.amount * Decimal("7.25") / Decimal(100), currency)
    elif region == "US-NY":
        t = Money(taxable.amount * Decimal("4.00") / Decimal(100), currency)
    elif region == "US-TX":
        t = Money(taxable.amount * Decimal("6.25") / Decimal(100), currency)
    elif region == "US-OR":
        t = Money(taxable.amount * Decimal("0") / Decimal(100), currency)
    elif region == "GB":
        t = Money(taxable.amount * Decimal("20") / Decimal(100), currency)
    elif region == "DE":
        t = Money(taxable.amount * Decimal("19") / Decimal(100), currency)
    else:
        # regions we do not charge tax in
        t = Money.zero(currency)
    # add the tax back on
    tot = taxable + t
    # build the text the storefront prints
    tmp = []
    tmp.append(f"Cart preview ({region})")
    for r in rows:
        tmp.append(f"  {r[0]:<10} {r[2]} x {r[1].amount} = {(r[1] * r[2]).amount}")
    tmp.append(f"  {'Subtotal':<10} {sub.amount} {currency}")
    if code_used:
        tmp.append(f"  {'Discount':<10} -{disc.amount} {currency} ({code_used})")
    tmp.append(f"  {'Tax':<10} {t.amount} {currency}")
    tmp.append(f"  {'Total':<10} {tot.amount} {currency}")
    # hand back the quote and the text
    return Receipt(
        quote=Quote(
            subtotal=sub,
            discount=disc,
            tax=t,
            total=tot,
            applied_codes=(code_used,) if code_used else (),
        ),
        text="\n".join(tmp),
    )
