"""Tests for sandbox/parking/domain/pricing.py."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from sandbox.parking.domain.pricing import (
    InvalidTransition,
    TicketStatus,
    billable_minutes,
    fee_for,
    transition,
)
from sandbox.parking.tests.conftest import CARD, EPOCH


def test_billable_minutes_rejects_exit_before_entry() -> None:
    with pytest.raises(ValueError):
        billable_minutes(EPOCH, EPOCH - timedelta(minutes=1))


def test_billable_minutes_rounds_up_a_partial_minute() -> None:
    assert billable_minutes(EPOCH, EPOCH + timedelta(seconds=61)) == 2


def test_fee_for_is_zero_at_the_grace_boundary() -> None:
    assert fee_for(CARD.grace_minutes, CARD) == Decimal("0.00")


def test_fee_for_charges_the_first_hour_just_past_grace() -> None:
    assert fee_for(CARD.grace_minutes + 1, CARD) == Decimal("4.00")


def test_fee_for_rounds_up_a_started_extra_hour() -> None:
    assert fee_for(61, CARD) == Decimal("6.00")


def test_fee_for_caps_at_the_daily_cap() -> None:
    assert fee_for(600, CARD) == Decimal("20.00")


def test_transition_allows_the_open_paid_exited_chain() -> None:
    assert transition(TicketStatus.OPEN, TicketStatus.PAID) is TicketStatus.PAID
    assert transition(TicketStatus.PAID, TicketStatus.EXITED) is TicketStatus.EXITED


def test_transition_rejects_skipping_a_step() -> None:
    with pytest.raises(InvalidTransition):
        transition(TicketStatus.OPEN, TicketStatus.EXITED)
