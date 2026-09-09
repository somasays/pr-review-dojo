"""Pure logic with no IO: cost math, the pacing check, and the campaign
status state machine. See sandbox/adserver/README.md for the conventions
this module follows."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

CENT = Decimal("0.01")


def cost_of(impressions: int, cpm: Decimal) -> Decimal:
    """The cost of serving `impressions` impressions at `cpm` (price per
    1000 impressions), in cents, rounded half up."""
    cost = (Decimal(impressions) * cpm) / Decimal(1000)
    return cost.quantize(CENT, rounding=ROUND_HALF_UP)


def can_serve(spent_today: Decimal, daily_budget: Decimal, cpm: Decimal) -> bool:
    """True while serving one more impression keeps today's spend at or
    under the daily budget."""
    return spent_today + cost_of(1, cpm) <= daily_budget


def remaining_budget(spent_today: Decimal, daily_budget: Decimal) -> Decimal:
    """The budget left today, never negative."""
    remaining = daily_budget - spent_today
    return remaining if remaining > Decimal("0.00") else Decimal("0.00")


def carryover_for(
    yesterday_budget: Decimal, yesterday_spent: Decimal, daily_budget: Decimal
) -> Decimal:
    """The unspent part of yesterday's budget, capped at one extra day's
    budget and never negative."""
    unspent = yesterday_budget - yesterday_spent
    if unspent <= Decimal("0.00"):
        return Decimal("0.00")
    return min(unspent, daily_budget)


def effective_budget(daily_budget: Decimal, carryover: Decimal) -> Decimal:
    """Today's budget plus any carried-over amount."""
    return daily_budget + carryover


def pace_target(daily_budget: Decimal, now_utc: datetime) -> Decimal:
    """The budget that should have been spent by now if spend were linear
    across the UTC calendar day: budget times the fraction of the day
    elapsed."""
    seconds_elapsed = now_utc.hour * 3600 + now_utc.minute * 60 + now_utc.second
    fraction = Decimal(seconds_elapsed) / Decimal(86400)
    return (daily_budget * fraction).quantize(CENT, rounding=ROUND_HALF_UP)


class CampaignStatus(Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"


class InvalidTransition(Exception):
    pass


_ALLOWED: dict[CampaignStatus, frozenset[CampaignStatus]] = {
    CampaignStatus.ACTIVE: frozenset({CampaignStatus.PAUSED, CampaignStatus.ENDED}),
    CampaignStatus.PAUSED: frozenset({CampaignStatus.ACTIVE, CampaignStatus.ENDED}),
    CampaignStatus.ENDED: frozenset(),
}


def transition(current: CampaignStatus, target: CampaignStatus) -> CampaignStatus:
    """The only function allowed to move a campaign's status. Raises
    InvalidTransition for any move not in _ALLOWED."""
    if target not in _ALLOWED.get(current, frozenset()):
        raise InvalidTransition(f"cannot move a campaign from {current.value} to {target.value}")
    return target
