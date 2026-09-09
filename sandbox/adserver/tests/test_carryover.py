"""Tests for the unspent budget carry-over feature."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from sandbox.adserver.db import Advertiser, Campaign, Spend
from sandbox.adserver.domain.pacing import CampaignStatus, carryover_for
from sandbox.adserver.service import CampaignService, ServingService
from sandbox.adserver.tests.conftest import OPS_EMAIL
from sandbox.adserver.tracker import SpendTracker

TODAY = datetime.now(UTC).date()
YESTERDAY = TODAY - timedelta(days=1)


def test_carryover_computed_for_an_underspent_day(
    session_factory: sessionmaker[Session], advertiser: Advertiser, db: Session
):
    campaign = Campaign(
        advertiser_id=advertiser.id,
        name="Carryover Sale",
        daily_budget=Decimal("10.00"),
        cpm=Decimal("2.00"),
        status=CampaignStatus.ACTIVE.value,
        carryover_enabled=True,
    )
    db.add(campaign)
    db.commit()
    db.add(Spend(campaign_id=campaign.id, day=YESTERDAY, impressions=300, amount=Decimal("6.00")))
    db.commit()

    service = CampaignService(session_factory, SpendTracker())
    row = service.apply_carryover(OPS_EMAIL, campaign.id, TODAY)

    assert row.amount == Decimal("4.00")


def test_carryover_for_caps_at_one_extra_days_budget():
    amount = carryover_for(Decimal("50.00"), Decimal("0.00"), Decimal("10.00"))
    assert amount == Decimal("10.00")


def test_serving_continues_past_the_daily_budget_when_carryover_applies(
    session_factory: sessionmaker[Session], advertiser: Advertiser, db: Session
):
    # cpm 1000.00 -> each impression costs exactly $1.00.
    campaign = Campaign(
        advertiser_id=advertiser.id,
        name="Tight Carryover",
        daily_budget=Decimal("3.00"),
        cpm=Decimal("1000.00"),
        status=CampaignStatus.ACTIVE.value,
        carryover_enabled=True,
    )
    db.add(campaign)
    db.commit()
    db.add(Spend(campaign_id=campaign.id, day=YESTERDAY, impressions=1, amount=Decimal("1.00")))
    db.commit()

    campaign_service = CampaignService(session_factory, SpendTracker())
    campaign_service.apply_carryover(OPS_EMAIL, campaign.id, TODAY)

    now = datetime(TODAY.year, TODAY.month, TODAY.day, 12, tzinfo=UTC)
    serving = ServingService(session_factory, SpendTracker())
    # Budget alone allows 3 impressions; the $2.00 carry-over allows 2 more.
    results = [serving.decide(campaign.id, now) for _ in range(6)]
    assert results == [True, True, True, True, True, False]
