"""Pricing math: pure logic with no IO.

See sandbox/parking/README.md for the conventions this module follows
(integer ids, Decimal money quantized to cents, aware UTC timestamps, and
the ticket lifecycle enforced only through `transition`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

CENTS = Decimal("0.01")
MINUTES_PER_HOUR = 60
MINUTES_PER_DAY = 1440


@dataclass(frozen=True, slots=True)
class RateCard:
    """One garage's pricing rule. All amounts are integer cents; `fee_for`
    converts to a quantized Decimal."""

    grace_minutes: int
    first_hour_cents: int
    extra_hour_cents: int
    daily_cap_cents: int
    lost_ticket_cents: int


def billable_minutes(entered_at: datetime, exited_at: datetime) -> int:
    """Minutes between `entered_at` and `exited_at`, rounded up to the next
    whole minute. Rejects an exit before the entry."""
    if exited_at < entered_at:
        raise ValueError("exited_at must not be before entered_at")
    seconds = (exited_at - entered_at).total_seconds()
    return math.ceil(seconds / 60)


def fee_for(minutes: int, card: RateCard) -> Decimal:
    """Fee for `minutes` of parking under `card`. Free within the grace
    period; otherwise the first hour costs `first_hour_cents` and each
    started extra hour after it costs `extra_hour_cents`. The total is
    capped at `daily_cap_cents` per started 24 hour day."""
    if minutes <= card.grace_minutes:
        return Decimal("0.00")

    if minutes <= MINUTES_PER_HOUR:
        cents = card.first_hour_cents
    else:
        extra_minutes = minutes - MINUTES_PER_HOUR
        extra_hours = math.ceil(extra_minutes / MINUTES_PER_HOUR)
        cents = card.first_hour_cents + extra_hours * card.extra_hour_cents

    days = math.ceil(minutes / MINUTES_PER_DAY)
    cents = min(cents, card.daily_cap_cents * days)
    return (Decimal(cents) / 100).quantize(CENTS, rounding=ROUND_HALF_UP)


def pass_covers(valid_from: datetime, valid_to: datetime, at: datetime) -> bool:
    """Whether `at` falls within a pass covering [valid_from, valid_to]."""
    return valid_from <= at <= valid_to


def pass_price(months: int, monthly_cents: int) -> Decimal:
    """Price for a pass covering `months` months at `monthly_cents` a month."""
    return (Decimal(months * monthly_cents) / 100).quantize(CENTS, rounding=ROUND_HALF_UP)


class TicketStatus(Enum):
    OPEN = "open"
    PAID = "paid"
    EXITED = "exited"


class InvalidTransition(Exception):
    pass


_ALLOWED = {
    TicketStatus.OPEN: TicketStatus.PAID,
    TicketStatus.PAID: TicketStatus.EXITED,
}


def transition(current: TicketStatus, target: TicketStatus) -> TicketStatus:
    """A ticket's lifecycle is open then paid then exited, and only this
    function may change it. Raises InvalidTransition for any other move,
    including staying put or skipping a step."""
    if _ALLOWED.get(current) is not target:
        raise InvalidTransition(f"cannot move a ticket from {current.value} to {target.value}")
    return target
