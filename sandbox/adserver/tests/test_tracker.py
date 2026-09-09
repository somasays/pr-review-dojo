"""Tests for SpendTracker and Flusher."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session, sessionmaker

from sandbox.adserver.db import Campaign
from sandbox.adserver.domain.pacing import cost_of
from sandbox.adserver.repo import SpendRepo
from sandbox.adserver.tracker import Flusher, SpendTracker

DAY = date(2026, 1, 1)


def test_record_under_threadpool_counts_equal_calls():
    tracker = SpendTracker()
    barrier = threading.Barrier(20)

    def hit() -> None:
        barrier.wait()
        tracker.record(1, DAY)

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(hit) for _ in range(20)]
        for f in futures:
            f.result()

    assert tracker.counts[(1, DAY)] == 20


def test_drain_atomically_clears_and_returns_counts():
    tracker = SpendTracker()
    tracker.record(1, DAY)
    tracker.record(1, DAY)
    tracker.record(2, DAY)

    drained = tracker.drain()

    assert drained == {(1, DAY): 2, (2, DAY): 1}
    assert tracker.counts == {}

    # A record after drain starts a fresh count in a fresh dict, so it
    # cannot retroactively change the dict already handed to the flusher.
    tracker.record(1, DAY)
    assert tracker.counts == {(1, DAY): 1}
    assert drained == {(1, DAY): 2, (2, DAY): 1}


def test_spent_today_combines_flushed_and_tracked_amounts():
    tracker = SpendTracker()
    tracker.record(1, DAY)
    tracker.record(1, DAY)
    spent = tracker.spent_today(1, DAY, Decimal("2.00"), Decimal("1.00"))
    assert spent == Decimal("1.00") + cost_of(2, Decimal("2.00"))


def test_flusher_run_once_upserts_a_spend_row(
    session_factory: sessionmaker[Session], campaign: Campaign
):
    tracker = SpendTracker()
    tracker.record(campaign.id, DAY)
    tracker.record(campaign.id, DAY)
    tracker.record(campaign.id, DAY)
    flusher = Flusher(session_factory, tracker, interval_seconds=9999)

    flusher.run_once()

    session = session_factory()
    spend = SpendRepo(session).for_campaign_day(campaign.id, DAY)
    session.close()

    assert spend is not None
    assert spend.impressions == 3
    assert spend.amount == cost_of(3, campaign.cpm)
    assert tracker.counts == {}


def test_flusher_run_once_puts_counts_back_on_failure(
    session_factory: sessionmaker[Session], campaign: Campaign, monkeypatch: pytest.MonkeyPatch
):
    tracker = SpendTracker()
    tracker.record(campaign.id, DAY)
    flusher = Flusher(session_factory, tracker, interval_seconds=9999)

    def _boom(self: SpendRepo, *args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(SpendRepo, "upsert", _boom)

    with pytest.raises(RuntimeError):
        flusher.run_once()

    assert tracker.counts == {(campaign.id, DAY): 1}
