"""Flash sale pricing: percent off a SKU within a time window, floored, and
capped per customer.

Pure functions and a frozen dataclass, no IO. The service layer decides which
sales are configured and tracks how many units each customer has bought.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.domain.money import Money


@dataclass(frozen=True, slots=True)
class FlashSale:
    """A time-boxed discount on one SKU.

    percent_off: percent knocked off the regular unit price.
    floor_price: the sale price never drops below this, no matter how deep
    the percent off would otherwise cut.
    per_customer_unit_cap: the most units one customer may buy at the sale
    price while the sale is running.
    """

    sku: str
    starts_at: datetime
    ends_at: datetime
    percent_off: Decimal
    floor_price: Money
    per_customer_unit_cap: int

    def __post_init__(self) -> None:
        if self.ends_at <= self.starts_at:
            raise ValueError(f"sale window is empty for {self.sku}")
        if not (Decimal("0") < self.percent_off <= Decimal("100")):
            raise ValueError(f"percent_off must be between 0 and 100 for {self.sku}")
        if self.per_customer_unit_cap <= 0:
            raise ValueError(f"per_customer_unit_cap must be positive for {self.sku}")

    def is_active(self, now: datetime) -> bool:
        """True while `now` is inside the sale window, both ends inclusive."""
        return self.starts_at <= now <= self.ends_at

    def sale_price(self, unit_price: Money) -> Money:
        """`unit_price` less `percent_off`, never below `floor_price`."""
        discounted = unit_price - unit_price.percent(self.percent_off)
        return discounted if discounted > self.floor_price else self.floor_price

    def units_within_cap(self, already_purchased: int, quantity: int) -> bool:
        """True if buying `quantity` more units keeps the customer at or under
        the per-customer cap for this sale."""
        return already_purchased + quantity < self.per_customer_unit_cap
