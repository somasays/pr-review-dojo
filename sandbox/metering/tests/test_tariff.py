"""domain/tariff.py: pure logic, no IO."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from sandbox.metering.domain.tariff import (
    Band,
    InvalidReading,
    Tariff,
    charge_for,
    consumption,
    period_days,
)

MAX_READING = Decimal("99999.999")

TARIFF = Tariff(
    standing_charge_per_day=Decimal("0.20"),
    bands=(
        Band(Decimal("100.000"), Decimal("0.30")),
        Band(Decimal("300.000"), Decimal("0.20")),
        Band(None, Decimal("0.10")),
    ),
)


def test_consumption_simple_delta() -> None:
    assert consumption(Decimal("100.000"), Decimal("142.500"), MAX_READING) == Decimal("42.500")


def test_consumption_rollover_past_max_reading() -> None:
    # Meter reads 99999.900, wraps to 0 at max_reading, then reads 0.050.
    result = consumption(Decimal("99999.900"), Decimal("0.050"), MAX_READING)
    assert result == Decimal("0.149")


def test_consumption_rejects_a_reading_outside_zero_to_max() -> None:
    with pytest.raises(InvalidReading):
        consumption(Decimal("10.000"), Decimal("-5.000"), MAX_READING)
    with pytest.raises(InvalidReading):
        consumption(Decimal("10.000"), MAX_READING + 1, MAX_READING)


def test_charge_for_progressive_bands_at_the_second_boundary() -> None:
    # 300.000 kWh lands exactly on the second band's threshold: 100 kWh
    # at the first band's rate, the remaining 200 at the second, none
    # spills into the open-ended third band.
    charge = charge_for(Decimal("300.000"), TARIFF, days=0)
    assert charge == Decimal("100") * Decimal("0.30") + Decimal("200") * Decimal("0.20")


def test_charge_for_standing_charge_scales_with_days() -> None:
    ten_days = charge_for(Decimal("0.000"), TARIFF, days=10)
    thirty_days = charge_for(Decimal("0.000"), TARIFF, days=30)
    assert ten_days == Decimal("2.00")
    assert thirty_days == Decimal("6.00")


def test_period_days_half_open() -> None:
    assert period_days(date(2026, 1, 1), date(2026, 2, 1)) == 31
    with pytest.raises(ValueError):
        period_days(date(2026, 2, 1), date(2026, 1, 1))
