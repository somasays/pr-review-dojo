"""Service-level tests: ServingService and CampaignService against an
in-memory database."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session, sessionmaker

from sandbox.adserver.db import Advertiser, Campaign
from sandbox.adserver.domain.pacing import CampaignStatus
from sandbox.adserver.service import CampaignService, NotAllowed, NotFound, ServingService
from sandbox.adserver.tracker import SpendTracker

OPS = "ops@example.com"
T0 = datetime(2026, 1, 1, 12, tzinfo=UTC)


def test_decide_records_an_impression_and_returns_true_under_budget(
    session_factory: sessionmaker[Session], campaign: Campaign
):
    tracker = SpendTracker()
    service = ServingService(session_factory, tracker)

    served = service.decide(campaign.id, T0)

    assert served is True
    assert tracker.counts[(campaign.id, T0.date())] == 1


def test_decide_stops_serving_once_the_budget_is_exhausted(
    session_factory: sessionmaker[Session], advertiser: Advertiser, db: Session
):
    # cpm 1000.00 -> each impression costs exactly $1.00, so a $3.00
    # budget fits exactly 3 impressions.
    campaign = Campaign(
        advertiser_id=advertiser.id,
        name="Tight Budget",
        daily_budget=Decimal("3.00"),
        cpm=Decimal("1000.00"),
        status=CampaignStatus.ACTIVE.value,
    )
    db.add(campaign)
    db.commit()
    tracker = SpendTracker()
    service = ServingService(session_factory, tracker)

    results = [service.decide(campaign.id, T0) for _ in range(4)]

    assert results == [True, True, True, False]


def test_decide_returns_false_for_a_paused_campaign_and_leaves_it_untracked(
    session_factory: sessionmaker[Session], campaign: Campaign, db: Session
):
    campaign.status = CampaignStatus.PAUSED.value
    db.commit()
    tracker = SpendTracker()
    service = ServingService(session_factory, tracker)

    assert service.decide(campaign.id, T0) is False
    assert tracker.counts == {}


def test_campaign_lifecycle_create_pause_resume_end(
    session_factory: sessionmaker[Session], advertiser: Advertiser
):
    service = CampaignService(session_factory)
    with pytest.raises(NotFound):
        service.create(OPS, "nobody@example.com", "Nope", Decimal("5.00"), Decimal("1.00"))

    campaign = service.create(
        OPS, advertiser.email, "Winter Sale", Decimal("5.00"), Decimal("1.50")
    )
    assert campaign.status == CampaignStatus.ACTIVE.value

    paused = service.pause(OPS, campaign.id)
    assert paused.status == CampaignStatus.PAUSED.value
    resumed = service.resume(OPS, campaign.id)
    assert resumed.status == CampaignStatus.ACTIVE.value
    ended = service.end(OPS, campaign.id)
    assert ended.status == CampaignStatus.ENDED.value
    with pytest.raises(NotAllowed):
        service.resume(OPS, campaign.id)


def test_unit_of_work_rolls_back_on_error(
    session_factory: sessionmaker[Session], advertiser: Advertiser
):
    from sandbox.adserver.db import unit_of_work
    from sandbox.adserver.repo import CampaignRepo

    with pytest.raises(RuntimeError):
        with unit_of_work(session_factory) as session:
            session.add(
                Campaign(
                    advertiser_id=advertiser.id,
                    name="Rolled Back",
                    daily_budget=Decimal("5.00"),
                    cpm=Decimal("1.00"),
                    status=CampaignStatus.ACTIVE.value,
                )
            )
            session.flush()
            raise RuntimeError("boom")

    check = session_factory()
    campaigns = CampaignRepo(check).for_advertiser(advertiser.id)
    check.close()
    assert all(c.name != "Rolled Back" for c in campaigns)
