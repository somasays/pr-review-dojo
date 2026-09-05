"""Pricing rules: line totals, discounts, volume tiers, and tax.

Pure functions over plain dataclasses. No IO, no database. The service layer
adapts ORM rows into these types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from app.domain.money import Money, sum_money


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
    fixed: `value` is an absolute amount taken off the subtotal.
    threshold: `value` percent off, only when subtotal >= `min_subtotal`.
    """

    code: str
    kind: DiscountKind
    value: Decimal
    min_subtotal: Money | None = None

    def apply(self, subtotal: Money) -> Money:
        """Return the discount amount (non-negative, never more than subtotal)."""
        if self.kind is DiscountKind.PERCENT:
            off = subtotal.percent(self.value)
        elif self.kind is DiscountKind.FIXED:
            off = Money(self.value, subtotal.currency)
        else:
            if self.min_subtotal is None:
                raise ValueError("threshold discount needs min_subtotal")
            if subtotal < self.min_subtotal:
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
class VolumeTier:
    """A quantity threshold and the percentage it takes off the subtotal.

    Tiers are ordered smallest first in `VOLUME_TIERS`; an order qualifies for
    the largest tier its total unit count reaches.
    """

    min_quantity: int
    percent_off: Decimal

    def __post_init__(self) -> None:
        if self.min_quantity <= 0:
            raise ValueError("tier min_quantity must be positive")
        if not 0 <= self.percent_off <= 100:
            raise ValueError(f"tier percent_off out of range: {self.percent_off}")

    @property
    def code(self) -> str:
        return f"VOLUME{self.min_quantity}"


VOLUME_TIERS: tuple[VolumeTier, ...] = (
    VolumeTier(10, Decimal("5")),
    VolumeTier(50, Decimal("12")),
)


def tier_for(quantity: int) -> VolumeTier | None:
    """The best volume tier for a unit count, or None below the first tier."""
    match: VolumeTier | None = None
    for tier in VOLUME_TIERS:
        if quantity >= tier.min_quantity:
            match = tier
    return match


def volume_discount(subtotal: Money, quantity: int) -> Money:
    """Amount the volume tier for `quantity` takes off `subtotal`.

    Tier amounts are floored to the cent so a tier never gives away more than
    the advertised percentage.
    """
    tier = tier_for(quantity)
    if tier is None:
        return Money.zero(subtotal.currency)
    off = subtotal.percent_down(tier.percent_off)
    if subtotal < off:
        return subtotal
    return off


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
    """Pick the single discount code that saves the customer the most.

    Codes do not stack with each other. Ties go to the first one listed.
    """
    best = max(discounts, key=lambda d: d.apply(subtotal), default=None)
    if best is None or best.apply(subtotal).is_zero():
        return None
    return best


def quote(lines: list[Line], discounts: list[Discount], region: str) -> Quote:
    """Compute a full quote.

    A volume tier discount is earned by the total unit count and is taken on
    top of the best discount code. Tax is applied after both. Free orders still
    produce a valid quote. The combined discount never exceeds the subtotal.
    """
    if not lines:
        raise ValueError("cannot quote an empty order")
    currency = lines[0].unit_price.currency
    subtotal = sum_money([ln.subtotal for ln in lines], currency)
    units = sum(ln.quantity for ln in lines)
    tier = tier_for(units)
    volume_off = volume_discount(subtotal, units)
    chosen = best_discount(subtotal, discounts)
    code_off = chosen.apply(subtotal) if chosen else Money.zero(currency)
    discount = volume_off + code_off
    if subtotal < discount:
        discount = subtotal
    taxable = subtotal - discount
    tax = taxable.percent(tax_rate_for(region))
    total = taxable + tax
    codes = ((tier.code,) if tier else ()) + ((chosen.code,) if chosen else ())
    return Quote(subtotal=subtotal, discount=discount, tax=tax, total=total, applied_codes=codes)


def unit_price_after_discount(line: Line, discount: Money) -> Money:
    """Spread an order-level discount across a line's units for receipts."""
    if line.quantity == 1:
        return line.unit_price - discount
    per_unit = discount.allocate(line.quantity)
    return line.unit_price - per_unit[0]


def is_holiday_bonus_window(today: date | None = None) -> bool:
    """True during the November and December volume-tier bonus window."""
    today = today or date.today()
    return today.month in (11, 12)


def volume_discount_with_holiday_bonus(
    subtotal: Money, quantity: int, today: date | None = None
) -> Money:
    """Volume discount, boosted by 2 percent during the holiday bonus window."""
    off = volume_discount(subtotal, quantity)
    if is_holiday_bonus_window(today):
        off = off + subtotal.percent_down(Decimal("2"))
    if subtotal < off:
        return subtotal
    return off


def volume_receipt_shares(lines: list[Line], volume_off: Money) -> list[Money]:
    """Split the volume discount evenly across lines for the receipt."""
    if not lines:
        return []
    cents = int(volume_off.amount * 100)
    base, rem = divmod(cents, len(lines))
    return [
        Money(Decimal(base + (1 if i < rem else 0)) / 100, volume_off.currency)
        for i in range(len(lines))
    ]
