"""Repository tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from sandbox.adserver.db import Advertiser, Campaign
from sandbox.adserver.domain.pacing import CampaignStatus
from sandbox.adserver.repo import AdvertiserRepo, CampaignRepo, SpendRepo

DAY = date(2026, 1, 1)


def test_advertiser_and_campaign_repo_scoping(db: Session, advertiser: Advertiser):
    assert AdvertiserRepo(db).by_email(advertiser.email).id == advertiser.id
    assert AdvertiserRepo(db).by_email("nobody@example.com") is None

    active = Campaign(
        advertiser_id=advertiser.id,
        name="Active",
        daily_budget=Decimal("5.00"),
        cpm=Decimal("1.00"),
        status=CampaignStatus.ACTIVE.value,
    )
    paused = Campaign(
        advertiser_id=advertiser.id,
        name="Paused",
        daily_budget=Decimal("5.00"),
        cpm=Decimal("1.00"),
        status=CampaignStatus.PAUSED.value,
    )
    db.add_all([active, paused])
    db.commit()

    repo = CampaignRepo(db)
    active_ids = {c.id for c in repo.active()}
    assert active.id in active_ids
    assert paused.id not in active_ids
    assert {c.id for c in repo.for_advertiser(advertiser.id)} == {active.id, paused.id}


def test_spend_repo_upsert_inserts_then_accumulates(db: Session, campaign: Campaign):
    repo = SpendRepo(db)
    repo.upsert(campaign.id, DAY, 10, Decimal("0.02"))
    repo.upsert(campaign.id, DAY, 5, Decimal("0.01"))

    row = repo.for_campaign_day(campaign.id, DAY)
    assert row is not None
    assert row.impressions == 15
    assert row.amount == Decimal("0.03")
    assert len(repo.for_campaign(campaign.id)) == 1
