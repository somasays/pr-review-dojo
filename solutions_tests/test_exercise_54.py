"""Hidden tests for exercise 54: unspent budget carry-over.

Each test is built against a fresh in-memory SQLite database so it does
not depend on any other test's state.
"""

from __future__ import annotations

import inspect
import threading
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sandbox.adserver.db import Advertiser, Base, Campaign, Carryover, Spend
from sandbox.adserver.domain.pacing import CampaignStatus, carryover_for
from sandbox.adserver.repo import CarryoverRepo
from sandbox.adserver.service import CampaignService, ServingService
from sandbox.adserver.tracker import SpendTracker

OPS_EMAIL = "ops@example.com"


def _session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _add_advertiser(factory, email: str = "adele@example.com") -> Advertiser:
    session = factory()
    advertiser = Advertiser(email=email)
    session.add(advertiser)
    session.commit()
    session.close()
    return advertiser


def _add_campaign(
    factory,
    advertiser_id: int,
    daily_budget: Decimal,
    cpm: Decimal,
    carryover_enabled: bool = False,
) -> Campaign:
    session = factory()
    campaign = Campaign(
        advertiser_id=advertiser_id,
        name="Test Campaign",
        daily_budget=daily_budget,
        cpm=cpm,
        status=CampaignStatus.ACTIVE.value,
        carryover_enabled=carryover_enabled,
    )
    session.add(campaign)
    session.commit()
    session.close()
    return campaign


def _add_spend(factory, campaign_id: int, day: date, impressions: int, amount: Decimal) -> None:
    session = factory()
    session.add(Spend(campaign_id=campaign_id, day=day, impressions=impressions, amount=amount))
    session.commit()
    session.close()


# --- Finding 1 (concurrency): the tracker read must go through a locked
# accessor, not raw dict access. ---------------------------------------


def test_apply_carryover_does_not_touch_tracker_counts_directly():
    assert hasattr(SpendTracker, "count_for"), "expected a locked SpendTracker.count_for accessor"
    import sandbox.adserver.service as service_module

    source = inspect.getsource(CampaignService.apply_carryover)
    assert "tracker.counts" not in source
    # Either apply_carryover calls count_for directly, or it goes through
    # a shared helper that does; either way the raw dict must stay hidden.
    module_source = inspect.getsource(service_module)
    assert "count_for" in module_source


def test_count_for_is_safe_under_a_concurrent_drain():
    tracker = SpendTracker()
    day = date(2026, 1, 1)
    for _ in range(5):
        tracker.record(1, day)

    release = threading.Event()
    drained_done = threading.Event()
    drained_holder: dict[str, dict] = {}

    def before_read() -> None:
        release.set()
        assert drained_done.wait(timeout=5), "the concurrent drain never completed"

    tracker._before_read = before_read  # type: ignore[attr-defined]

    def do_drain() -> None:
        assert release.wait(timeout=5), "count_for never signalled readiness"
        drained_holder["drained"] = tracker.drain()
        drained_done.set()

    drain_thread = threading.Thread(target=do_drain)
    drain_thread.start()
    count = tracker.count_for(1, day)
    drain_thread.join(timeout=5)

    drained = drained_holder.get("drained", {})
    # The drain is forced to complete before count_for takes its lock, so
    # the 5 recorded impressions show up exactly once: drained, not read.
    assert count == 0
    assert drained.get((1, day)) == 5


# --- Finding 2 (idempotency): calling apply_carryover twice for the
# same day must not raise and must not create a second row. ------------


def test_apply_carryover_is_idempotent_for_the_same_day(monkeypatch: pytest.MonkeyPatch):
    factory = _session_factory()
    advertiser = _add_advertiser(factory)
    campaign = _add_campaign(factory, advertiser.id, Decimal("10.00"), Decimal("2.00"), True)
    today = date.today()
    yesterday = today - timedelta(days=1)
    _add_spend(factory, campaign.id, yesterday, impressions=300, amount=Decimal("6.00"))

    service = CampaignService(factory, SpendTracker())
    first = service.apply_carryover(OPS_EMAIL, campaign.id, today)

    # Simulate a second scheduler call that raced past the "does a row
    # already exist" check before the first call's insert became visible.
    calls = {"n": 0}
    original = CarryoverRepo.for_campaign_day

    def racy_check(self, campaign_id, day):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return original(self, campaign_id, day)

    monkeypatch.setattr(CarryoverRepo, "for_campaign_day", racy_check)
    second = service.apply_carryover(OPS_EMAIL, campaign.id, today)

    assert second.amount == first.amount == Decimal("4.00")

    session = factory()
    rows = session.query(Carryover).filter_by(campaign_id=campaign.id, day=today).all()
    session.close()
    assert len(rows) == 1


# --- Finding 3 (logic): carry-over must not compound past one extra
# day's budget. ----------------------------------------------------------


def test_second_days_carryover_does_not_exceed_one_daily_budget():
    factory = _session_factory()
    advertiser = _add_advertiser(factory)
    campaign = _add_campaign(factory, advertiser.id, Decimal("10.00"), Decimal("1.00"), True)
    real_today = datetime.now(UTC).date()
    day0 = real_today - timedelta(days=2)
    day1 = real_today - timedelta(days=1)
    day2 = real_today

    _add_spend(factory, campaign.id, day0, impressions=0, amount=Decimal("0.00"))
    service = CampaignService(factory, SpendTracker())
    first = service.apply_carryover(OPS_EMAIL, campaign.id, day1)
    assert first.amount == Decimal("10.00")

    # Day 1 spends more than the base daily budget, using up its carried
    # over surplus, so day 2 should carry nothing forward.
    _add_spend(factory, campaign.id, day1, impressions=15, amount=Decimal("15.00"))
    second = service.apply_carryover(OPS_EMAIL, campaign.id, day2)

    assert second.amount <= campaign.daily_budget
    assert second.amount == Decimal("0.00")


# --- Finding 4 (API): only ops may trigger carry-over. ------------------


def test_carryover_endpoint_rejects_an_advertiser_key(monkeypatch: pytest.MonkeyPatch):
    from sandbox.adserver import api

    ops_key, adv_email, adv_key = "ops-test-key", "adele@example.com", "adv-test-key"
    monkeypatch.setenv(
        "ADSERVER_KEYS", f"ops:ops@example.com:{ops_key},advertiser:{adv_email}:{adv_key}"
    )
    factory = _session_factory()
    _add_advertiser(factory, adv_email)

    api.app.dependency_overrides[api.get_session_factory] = lambda: factory
    api.app.state.flusher.session_factory = factory
    api.app.state.tracker.counts.clear()
    try:
        with TestClient(api.app) as client:
            created = client.post(
                "/campaigns",
                json={
                    "advertiser_email": adv_email,
                    "name": "Race",
                    "daily_budget": "10.00",
                    "cpm": "2.00",
                    "carryover_enabled": True,
                },
                headers={"X-Adserver-Key": ops_key},
            )
            assert created.status_code == 201
            campaign_id = created.json()["id"]

            resp = client.post(
                f"/campaigns/{campaign_id}/carryover", headers={"X-Adserver-Key": adv_key}
            )
    finally:
        api.app.dependency_overrides.clear()

    assert resp.status_code == 403


# --- Findings 5, 6, 7 (structure): no wall-clock call inside the
# function, no string status literal, and one shared spend helper. -------


def test_apply_carryover_derives_yesterday_from_the_today_parameter():
    source = inspect.getsource(CampaignService.apply_carryover)
    assert "datetime.now" not in source


def test_apply_carryover_does_not_compare_status_to_a_string_literal():
    source = inspect.getsource(CampaignService.apply_carryover)
    assert '"active"' not in source


def test_decide_and_apply_carryover_share_one_spend_helper():
    import sandbox.adserver.service as service_module

    decide_source = inspect.getsource(ServingService.decide)
    carryover_source = inspect.getsource(CampaignService.apply_carryover)
    helper_names = [
        name
        for name, obj in vars(service_module).items()
        if inspect.isfunction(obj)
        and obj.__module__ == service_module.__name__
        and name not in ("decide", "apply_carryover")
    ]
    shared = [name for name in helper_names if name in decide_source and name in carryover_source]
    assert shared, "expected one helper function referenced by both decide and apply_carryover"


# --- Finding 8 (tests): edge cases the shipped tests skipped. -----------


def test_carryover_for_is_zero_when_exactly_the_budget_was_spent():
    assert carryover_for(Decimal("10.00"), Decimal("10.00"), Decimal("10.00")) == Decimal("0.00")


def test_carryover_for_clamps_negative_unspent_to_zero():
    assert carryover_for(Decimal("10.00"), Decimal("15.00"), Decimal("10.00")) == Decimal("0.00")
