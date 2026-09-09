"""Serving decisions and campaign lifecycle: business rules layered on
the repositories and the in-process spend tracker. Each public method
opens exactly one unit of work."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from sandbox.adserver.db import Campaign, unit_of_work
from sandbox.adserver.domain.pacing import CampaignStatus, InvalidTransition, can_serve
from sandbox.adserver.domain.pacing import transition as transition_status
from sandbox.adserver.repo import AdvertiserRepo, CampaignRepo, SpendRepo
from sandbox.adserver.tracker import SpendTracker


class NotFound(Exception):
    pass


class NotAllowed(Exception):
    pass


class ServingService:
    def __init__(self, session_factory: sessionmaker[Session], tracker: SpendTracker) -> None:
        self.session_factory = session_factory
        self.tracker = tracker

    def decide(self, campaign_id: int, now: datetime) -> bool:
        """True, and an impression recorded, when the campaign is active
        and one more impression keeps today's spend at or under budget."""
        day = now.date()
        with unit_of_work(self.session_factory) as session:
            campaign = CampaignRepo(session).get(campaign_id)
            if campaign is None:
                raise NotFound(f"campaign {campaign_id} not found")
            if CampaignStatus(campaign.status) is not CampaignStatus.ACTIVE:
                return False
            flushed = SpendRepo(session).for_campaign_day(campaign_id, day)
            flushed_amount = flushed.amount if flushed is not None else Decimal("0.00")
            cpm, daily_budget = campaign.cpm, campaign.daily_budget

        spent_today = self.tracker.spent_today(campaign_id, day, cpm, flushed_amount)
        if can_serve(spent_today, daily_budget, cpm):
            self.tracker.record(campaign_id, day)
            return True
        return False


class CampaignService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create(
        self, ops_email: str, advertiser_email: str, name: str, daily_budget: Decimal, cpm: Decimal
    ) -> Campaign:
        with unit_of_work(self.session_factory) as session:
            advertiser = AdvertiserRepo(session).by_email(advertiser_email)
            if advertiser is None:
                raise NotFound(f"advertiser {advertiser_email} not found")
            campaign = Campaign(
                advertiser_id=advertiser.id,
                name=name,
                daily_budget=daily_budget,
                cpm=cpm,
                status=CampaignStatus.ACTIVE.value,
            )
            return CampaignRepo(session).add(campaign)

    def _move(self, campaign_id: int, target: CampaignStatus) -> Campaign:
        with unit_of_work(self.session_factory) as session:
            campaign = CampaignRepo(session).get(campaign_id)
            if campaign is None:
                raise NotFound(f"campaign {campaign_id} not found")
            try:
                campaign.status = transition_status(CampaignStatus(campaign.status), target).value
            except InvalidTransition as exc:
                raise NotAllowed(str(exc)) from exc
            session.flush()
            return campaign

    def pause(self, ops_email: str, campaign_id: int) -> Campaign:
        return self._move(campaign_id, CampaignStatus.PAUSED)

    def resume(self, ops_email: str, campaign_id: int) -> Campaign:
        return self._move(campaign_id, CampaignStatus.ACTIVE)

    def end(self, ops_email: str, campaign_id: int) -> Campaign:
        return self._move(campaign_id, CampaignStatus.ENDED)
