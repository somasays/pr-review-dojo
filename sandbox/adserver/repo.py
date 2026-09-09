"""Repositories: the only place that builds queries. Bound parameters
only, flush but never commit; the service owns the transaction through
db.unit_of_work()."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from sandbox.adserver.db import Advertiser, Campaign, Spend
from sandbox.adserver.domain.pacing import CampaignStatus


class AdvertiserRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def by_email(self, email: str) -> Advertiser | None:
        stmt = select(Advertiser).where(Advertiser.email == email)
        return self.session.scalars(stmt).first()


class CampaignRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, campaign: Campaign) -> Campaign:
        self.session.add(campaign)
        self.session.flush()
        return campaign

    def get(self, campaign_id: int) -> Campaign | None:
        return self.session.get(Campaign, campaign_id)

    def active(self) -> Sequence[Campaign]:
        stmt = select(Campaign).where(Campaign.status == CampaignStatus.ACTIVE.value)
        return self.session.scalars(stmt).all()

    def for_advertiser(self, advertiser_id: int) -> Sequence[Campaign]:
        stmt = select(Campaign).where(Campaign.advertiser_id == advertiser_id)
        return self.session.scalars(stmt).all()


class SpendRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, campaign_id: int, day: date, impressions: int, amount: Decimal) -> Spend:
        """Insert a new spend row, or add to the existing one for this
        campaign and day."""
        existing = self.for_campaign_day(campaign_id, day)
        if existing is not None:
            existing.impressions += impressions
            existing.amount += amount
            self.session.flush()
            return existing
        row = Spend(campaign_id=campaign_id, day=day, impressions=impressions, amount=amount)
        self.session.add(row)
        self.session.flush()
        return row

    def for_campaign_day(self, campaign_id: int, day: date) -> Spend | None:
        stmt = select(Spend).where(Spend.campaign_id == campaign_id, Spend.day == day)
        return self.session.scalars(stmt).first()

    def for_campaign(self, campaign_id: int) -> Sequence[Spend]:
        stmt = select(Spend).where(Spend.campaign_id == campaign_id).order_by(Spend.day)
        return self.session.scalars(stmt).all()
