"""Exchange rates and currency conversion.

A rate is published as "one unit of `base` buys `rate` units of `quote`".
The table falls back to the inverse when only one direction is published, so
finance only has to send us one row per pair.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.domain.money import Money

__all__ = ["ExchangeRate", "RateTable", "UnknownRate"]

# The provider sends six decimal places and we keep all of them.
RATE_SCALE = Decimal("0.000001")


class UnknownRate(LookupError):
    """Raised when no published rate connects two currencies."""

    def __init__(self, base: str, quote: str) -> None:
        super().__init__(f"no published rate for {base} to {quote}")
        self.base = base
        self.quote = quote


@dataclass(frozen=True, slots=True)
class ExchangeRate:
    """One published rate for one day."""

    base: str
    quote: str
    rate: Decimal
    as_of: date

    def __post_init__(self) -> None:
        if self.base == self.quote:
            raise ValueError("an exchange rate needs two different currencies")
        if self.rate <= 0:
            raise ValueError(f"rate must be positive for {self.base}/{self.quote}")
        object.__setattr__(self, "rate", self.rate.quantize(RATE_SCALE, rounding=ROUND_HALF_UP))

    def inverted(self) -> ExchangeRate:
        """The same pair quoted the other way round."""
        return ExchangeRate(self.quote, self.base, Decimal(1) / self.rate, self.as_of)


@dataclass(frozen=True, slots=True)
class RateTable:
    """The set of rates a quote is priced against."""

    rates: tuple[ExchangeRate, ...] = ()

    def rate_for(self, base: str, quote: str) -> Decimal:
        """The multiplier that turns an amount in `base` into one in `quote`."""
        if base == quote:
            return Decimal(1)
        for published in self.rates:
            if published.base == base and published.quote == quote:
                return published.rate
        for published in self.rates:
            if published.base == quote and published.quote == base:
                return published.inverted().rate
        raise UnknownRate(base, quote)

    def convert(self, money: Money, to_currency: str) -> Money:
        """Convert `money` into `to_currency` at the published rate."""
        if money.currency == to_currency:
            return money
        return Money(money.amount * self.rate_for(money.currency, to_currency), to_currency)

    def currencies(self) -> set[str]:
        """Every currency this table can price."""
        out: set[str] = set()
        for published in self.rates:
            out.add(published.base)
            out.add(published.quote)
        return out

    def is_stale(self, base: str, quote: str, today: date, max_age_days: int = 1) -> bool:
        """True when the published rate for this pair is older than `max_age_days` as of `today`."""
        match = next((p for p in self.rates if p.base == base and p.quote == quote), None)
        return match is not None and (today - match.as_of).days > max_age_days

    def rate_note(self, rate: ExchangeRate, amount: Decimal) -> str:
        """Render the note that explains which rate priced a converted amount."""
        pair = f"{rate.base}/{rate.quote}"
        return f"{amount} {rate.base} at {rate.rate} {pair} (published {rate.as_of.isoformat()})"
