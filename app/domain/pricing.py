"""Pricing rules: line totals, discounts, and tax.

Pure functions over plain dataclasses. No IO, no database. The service layer
adapts ORM rows into these types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from app.domain.money import CENTS, CurrencyMismatch, Money, sum_money


class DiscountKind(StrEnum):
    PERCENT = "percent"
    FIXED = "fixed"
    THRESHOLD = "threshold"


@dataclass(frozen=True, slots=True)
class Line:
    sku: str
    unit_price: Money
    quantity: int

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive for {self.sku}")
        if self.unit_price.is_negative():
            raise ValueError(f"negative unit price for {self.sku}")

    @property
    def subtotal(self) -> Money:
        return self.unit_price * self.quantity


@dataclass(frozen=True, slots=True)
class Discount:
    """A discount rule.

    percent: `value` is a percentage of the subtotal (0 to 100).
    fixed: `value` is an absolute amount taken off the subtotal, in whatever
      currency the subtotal is quoted in.
    threshold: `value` percent off, only when subtotal >= `min_subtotal`. A
      threshold code with no `min_subtotal` applies to every order.
    """

    code: str
    kind: DiscountKind
    value: Decimal
    min_subtotal: Money | None = None

    def __post_init__(self) -> None:
        if self.kind is DiscountKind.PERCENT and self.value > 100:
            raise ValueError(f"percent discount {self.code} is above 100")

    def apply(self, subtotal: Money) -> Money:
        """Return the discount amount (non-negative, never more than subtotal)."""
        if self.kind is DiscountKind.PERCENT:
            off = subtotal.percent(self.value)
        elif self.kind is DiscountKind.FIXED:
            off = Money(self.value, subtotal.currency)
        else:
            if self.min_subtotal is None or subtotal < self.min_subtotal:
                return Money.zero(subtotal.currency)
            off = subtotal.percent(self.value)
        if subtotal < off:
            return subtotal
        return off


TAX_RATES: dict[str, Decimal] = {
    "US-CA": Decimal("7.25"),
    "US-NY": Decimal("4.00"),
    "US-TX": Decimal("6.25"),
    "US-OR": Decimal("0"),
    "GB": Decimal("20"),
    "DE": Decimal("19"),
}


def tax_rate_for(region: str) -> Decimal:
    """Percent tax rate for a region. Unknown regions get zero tax."""
    return TAX_RATES.get(region, Decimal("0"))


@dataclass(frozen=True, slots=True)
class Quote:
    subtotal: Money
    discount: Money
    tax: Money
    total: Money
    applied_codes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def taxable(self) -> Money:
        return self.subtotal - self.discount


def best_discount(subtotal: Money, discounts: list[Discount]) -> Discount | None:
    """Pick the single discount that saves the customer the most.

    Discounts do not stack. Ties go to the first one listed.
    """
    best: Discount | None = None
    best_off = Money.zero(subtotal.currency)
    for d in discounts:
        off = d.apply(subtotal)
        if best_off < off:
            best, best_off = d, off
    return best


def quote(lines: list[Line], discounts: list[Discount], region: str) -> Quote:
    """Compute a full quote.

    Tax is applied after discount. Free orders still produce a valid quote.
    """
    if not lines:
        raise ValueError("cannot quote an empty order")
    currency = lines[0].unit_price.currency
    mixed = sorted({ln.unit_price.currency for ln in lines} - {currency})
    if mixed:
        raise CurrencyMismatch(f"quote mixes {currency} with {', '.join(mixed)}")
    subtotal = sum_money([ln.subtotal for ln in lines], currency)
    chosen = best_discount(subtotal, discounts)
    discount = chosen.apply(subtotal) if chosen else Money.zero(currency)
    taxable = subtotal - discount
    tax = taxable.percent(tax_rate_for(region))
    total = taxable + tax
    codes = (chosen.code,) if chosen else ()
    return Quote(subtotal=subtotal, discount=discount, tax=tax, total=total, applied_codes=codes)


def unit_price_after_discount(line: Line, discount: Money) -> Money:
    """Spread an order-level discount across a line's units for receipts."""
    if line.quantity == 1:
        return line.unit_price - discount
    per_unit = discount.allocate(line.quantity)
    return line.unit_price - per_unit[0]


FREE_SHIPPING_MIN = 49.99
"""Order value, in USD, that earns free shipping."""


def qualifies_for_free_shipping(subtotal: Money) -> bool:
    """True when a subtotal already converted to USD clears the minimum."""
    return subtotal.amount >= FREE_SHIPPING_MIN


def line_tax(line: Line, rate: Decimal) -> Money:
    """Tax owed on a single line, for itemized receipts."""
    return Money((line.subtotal.amount * rate / 100).quantize(CENTS), line.subtotal.currency)


def line_shares(lines: list[Line], discount: Money) -> list[Money]:
    """Prorate an order-level discount across lines by their subtotal."""
    subtotal = sum_money([ln.subtotal for ln in lines], discount.currency)
    return [discount * (ln.subtotal.amount / subtotal.amount) for ln in lines]
