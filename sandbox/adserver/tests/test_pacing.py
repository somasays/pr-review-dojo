"""Tests for the pure pacing domain logic."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from sandbox.adserver.domain.pacing import (
    CampaignStatus,
    InvalidTransition,
    can_serve,
    cost_of,
    pace_target,
    remaining_budget,
    transition,
)


def test_cost_of_rounds_half_up():
    assert cost_of(3, Decimal("2.00")) == Decimal("0.01")
    assert cost_of(1000, Decimal("2.00")) == Decimal("2.00")


def test_can_serve_true_at_boundary_and_false_just_past_it():
    cpm = Decimal("500.00")  # one impression costs exactly $0.50
    spent_at_boundary = Decimal("10.00") - cost_of(1, cpm)
    assert can_serve(spent_at_boundary, Decimal("10.00"), cpm)
    assert not can_serve(Decimal("10.00"), Decimal("10.00"), cpm)


def test_remaining_budget_never_negative():
    assert remaining_budget(Decimal("12.00"), Decimal("10.00")) == Decimal("0.00")
    assert remaining_budget(Decimal("4.00"), Decimal("10.00")) == Decimal("6.00")


def test_pace_target_is_zero_at_day_start_and_nearly_full_at_day_end():
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 23, 59, 0, tzinfo=UTC)
    assert pace_target(Decimal("10.00"), start) == Decimal("0.00")
    assert Decimal("9.99") <= pace_target(Decimal("10.00"), end) < Decimal("10.00")


def test_transition_follows_the_allowed_paths_and_rejects_others():
    assert transition(CampaignStatus.ACTIVE, CampaignStatus.PAUSED) is CampaignStatus.PAUSED
    assert transition(CampaignStatus.PAUSED, CampaignStatus.ACTIVE) is CampaignStatus.ACTIVE
    assert transition(CampaignStatus.ACTIVE, CampaignStatus.ENDED) is CampaignStatus.ENDED
    with pytest.raises(InvalidTransition):
        transition(CampaignStatus.ENDED, CampaignStatus.ACTIVE)
