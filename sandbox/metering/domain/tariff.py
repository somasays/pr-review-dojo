"""Tariff math: pure logic with no IO.

See sandbox/metering/README.md for why this package's conventions (integer
ids, an append-only reading ledger, Decimal kWh at 3 places, Decimal money
quantized to cents) differ from app/domain and the other sandboxes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

KWH_PLACES = Decimal("0.001")
CENTS = Decimal("0.01")


class InvalidReading(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Band:
    """One consumption band. `upto_kwh` is the cumulative kWh at which
    this band ends; `None` marks the last, open-ended band. Within a
    Tariff, bands are ordered ascending by `upto_kwh`."""

    upto_kwh: Decimal | None
    rate_per_kwh: Decimal


@dataclass(frozen=True, slots=True)
class Tariff:
    standing_charge_per_day: Decimal
    bands: tuple[Band, ...]

    def __post_init__(self) -> None:
        if not self.bands:
            raise ValueError("a tariff must have at least one band")
        if self.bands[-1].upto_kwh is not None:
            raise ValueError("the last band must be open-ended (upto_kwh=None)")
        previous_upto: Decimal | None = None
        for band in self.bands[:-1]:
            if band.upto_kwh is None:
                raise ValueError("only the last band may be open-ended")
            if previous_upto is not None and band.upto_kwh <= previous_upto:
                raise ValueError("bands must be strictly ascending by upto_kwh")
            previous_upto = band.upto_kwh


def period_days(start: date, end: date) -> int:
    """Number of days in the half-open period [start, end)."""
    if end <= start:
        raise ValueError("end must be after start for a half-open period")
    return (end - start).days


def consumption(
    previous_reading: Decimal, current_reading: Decimal, max_reading: Decimal
) -> Decimal:
    """kWh consumed between two meter readings. A meter counts up from 0
    to `max_reading`, then wraps back to 0; `current_reading` below
    `previous_reading` means exactly one rollover happened, and the
    consumption is the distance up to `max_reading` plus the distance
    past the wrap. A reading outside `[0, max_reading]` is invalid."""
    if max_reading <= 0:
        raise InvalidReading("max_reading must be positive")
    if not (0 <= previous_reading <= max_reading):
        raise InvalidReading(f"previous_reading {previous_reading} is outside [0, {max_reading}]")
    if not (0 <= current_reading <= max_reading):
        raise InvalidReading(f"current_reading {current_reading} is outside [0, {max_reading}]")

    if current_reading >= previous_reading:
        return current_reading - previous_reading
    return (max_reading - previous_reading) + current_reading


def adjustment_amount(original_amount: Decimal, recomputed_amount: Decimal) -> Decimal:
    """Signed adjustment amount, quantized to cents: positive when the
    correction raised the bill and more is owed, negative when it
    lowered the bill and a credit is due."""
    return (original_amount - recomputed_amount).quantize(CENTS, rounding=ROUND_HALF_UP)


def charge_for(kwh: Decimal, tariff: Tariff, days: int) -> Decimal:
    """Charge for `kwh` over `days` days under `tariff`. Bands apply
    progressively: each band's worth of kWh at its own rate, in order,
    with the open-ended last band absorbing whatever remains. The
    standing charge (`standing_charge_per_day * days`) is added once, and
    the total is quantized to cents, rounding half up."""
    if kwh < 0:
        raise InvalidReading("kwh must not be negative")
    if days < 0:
        raise ValueError("days must not be negative")

    remaining = kwh
    charge = Decimal("0")
    band_floor = Decimal("0")
    for band in tariff.bands:
        band_ceiling = band.upto_kwh if band.upto_kwh is not None else band_floor + remaining
        band_capacity = band_ceiling - band_floor
        band_kwh = min(remaining, band_capacity)
        if band_kwh > 0:
            charge += band_kwh * band.rate_per_kwh
            remaining -= band_kwh
        band_floor = band_ceiling
        if remaining <= 0:
            break

    charge += tariff.standing_charge_per_day * days
    return charge.quantize(CENTS, rounding=ROUND_HALF_UP)
