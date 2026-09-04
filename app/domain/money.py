"""Money type backed by Decimal.

All monetary values in the application go through this type. Floats are never
used for money; see README conventions.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Self

CENTS = Decimal("0.01")
WHOLE_UNIT = Decimal("1")


class CurrencyMismatch(ValueError):
    """Raised when arithmetic mixes two currencies."""


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError("Money.amount must be a Decimal, use Money.of() for other inputs")
        if len(self.currency) != 3 or not self.currency.isupper():
            raise ValueError(f"invalid currency code: {self.currency!r}")
        object.__setattr__(self, "amount", self.amount.quantize(CENTS, rounding=ROUND_HALF_UP))

    @classmethod
    def of(cls, value: int | str | Decimal, currency: str = "USD") -> Self:
        """Build from an int, str, or Decimal. Floats are rejected on purpose."""
        if isinstance(value, float):
            raise TypeError("refusing to build Money from float, pass a str or Decimal")
        return cls(Decimal(value), currency)

    @classmethod
    def zero(cls, currency: str = "USD") -> Self:
        return cls(Decimal("0"), currency)

    def _check(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(f"{self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: int | Decimal) -> Money:
        if isinstance(factor, float):
            raise TypeError("multiply Money by int or Decimal, not float")
        return Money(self.amount * Decimal(factor), self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.amount <= other.amount

    def is_zero(self) -> bool:
        return self.amount == 0

    def is_negative(self) -> bool:
        return self.amount < 0

    def percent(self, pct: Decimal) -> Money:
        """Return pct percent of this amount, rounded half up to cents."""
        return Money(self.amount * pct / Decimal(100), self.currency)

    def to_cents(self) -> int:
        """The amount in minor units, the shape the payment provider expects."""
        return int(self.amount * 100)

    def round_down(self) -> Money:
        """Return the amount rounded down to a whole unit.

        Used for currencies that are quoted without minor units, where we would
        rather keep the fraction than hand it to the customer.
        """
        return Money(self.amount.quantize(WHOLE_UNIT, rounding=ROUND_DOWN), self.currency)

    def allocate_by(self, weights: list[int]) -> list[Money]:
        """Split proportionally to `weights` so the parts sum to the original.

        Remainder cents are distributed to the first buckets.
        """
        if not weights or any(w < 0 for w in weights):
            raise ValueError("weights must be non-negative")
        total_weight = sum(weights)
        if total_weight == 0:
            raise ValueError("weights must not all be zero")
        cents = int(self.amount * 100)
        sign = -1 if cents < 0 else 1
        shares = [abs(cents) * w // total_weight for w in weights]
        rem = abs(cents) - sum(shares)
        out = []
        for i, share in enumerate(shares):
            c = share + (1 if i < rem else 0)
            out.append(Money(Decimal(sign * c) / 100, self.currency))
        return out

    def allocate(self, parts: int) -> list[Money]:
        """Split into `parts` amounts that sum exactly to the original.

        Remainder cents are distributed to the first buckets.
        """
        if parts <= 0:
            raise ValueError("parts must be positive")
        cents = int(self.amount * 100)
        base, rem = divmod(abs(cents), parts)
        sign = -1 if cents < 0 else 1
        out = []
        for i in range(parts):
            c = base + (1 if i < rem else 0)
            out.append(Money(Decimal(sign * c) / 100, self.currency))
        return out

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"


def sum_money(items: list[Money], currency: str = "USD") -> Money:
    total = Money.zero(currency)
    for item in items:
        total = total + item
    return total
