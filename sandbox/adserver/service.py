"""Serving decisions and campaign lifecycle: business rules layered on
the repositories and the in-process spend tracker. Each public method
opens exactly one unit of work."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from sandbox.adserver.db import Campaign, Carryover, unit_of_work
from sandbox.adserver.domain.pacing import (
    CampaignStatus,
    InvalidTransition,
    can_serve,
    carryover_for,
    cost_of,
    effective_budget,
)
from sandbox.adserver.domain.pacing import transition as transition_status
from sandbox.adserver.repo import AdvertiserRepo, CampaignRepo, CarryoverRepo, SpendRepo
from sandbox.adserver.tracker import SpendTracker


class NotFound(Exception):
    pass


class NotAllowed(Exception):
    pass


def _spend_so_far(
    spend_repo: SpendRepo, tracker: SpendTracker, campaign_id: int, day: date, cpm: Decimal
) -> Decimal:
    """Flushed spend for the day plus the tracker's count for it, priced
    at cpm. Shared by decide() and apply_carryover()."""
    flushed = spend_repo.for_campaign_day(campaign_id, day)
    flushed_amount = flushed.amount if flushed is not None else Decimal("0.00")
    return flushed_amount + cost_of(tracker.count_for(campaign_id, day), cpm)


class ServingService:
    def __init__(self, session_factory: sessionmaker[Session], tracker: SpendTracker) -> None:
        self.session_factory = session_factory
        self.tracker = tracker

    def decide(self, campaign_id: int, now: datetime) -> bool:
        """True, and an impression recorded, when the campaign is active
        and one more impression keeps today's spend at or under the
        effective budget (the daily budget plus any carry-over)."""
        day = now.date()
        with unit_of_work(self.session_factory) as session:
            campaign = CampaignRepo(session).get(campaign_id)
            if campaign is None:
                raise NotFound(f"campaign {campaign_id} not found")
            if CampaignStatus(campaign.status) is not CampaignStatus.ACTIVE:
                return False
            carryover_row = CarryoverRepo(session).for_campaign_day(campaign_id, day)
            carryover_amount = (
                carryover_row.amount if carryover_row is not None else Decimal("0.00")
            )
            cpm, daily_budget = campaign.cpm, campaign.daily_budget
            spent_today = _spend_so_far(SpendRepo(session), self.tracker, campaign_id, day, cpm)

        budget_today = effective_budget(daily_budget, carryover_amount)
        if can_serve(spent_today, budget_today, cpm):
            self.tracker.record(campaign_id, day)
            return True
        return False


class CampaignService:
    def __init__(self, session_factory: sessionmaker[Session], tracker: SpendTracker) -> None:
        self.session_factory = session_factory
        self.tracker = tracker

    def create(
        self,
        ops_email: str,
        advertiser_email: str,
        name: str,
        daily_budget: Decimal,
        cpm: Decimal,
        carryover_enabled: bool = False,
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
                carryover_enabled=carryover_enabled,
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

    def apply_carryover(self, ops_email: str, campaign_id: int, today: date) -> Carryover:
        """Compute and store today's carry-over: the unspent part of
        yesterday's daily budget, capped at one extra day's budget.
        Meant to be called once per day by a scheduler."""
        yesterday = today - timedelta(days=1)
        with unit_of_work(self.session_factory) as session:
            campaign = CampaignRepo(session).get(campaign_id)
            if campaign is None:
                raise NotFound(f"campaign {campaign_id} not found")
            if not campaign.carryover_enabled:
                raise NotAllowed(f"campaign {campaign_id} has not opted into carry-over")
            if CampaignStatus(campaign.status) is CampaignStatus.ACTIVE:
                carryover_repo = CarryoverRepo(session)
                existing = carryover_repo.for_campaign_day(campaign_id, today)
                if existing is not None:
                    return existing

                yesterday_spent = _spend_so_far(
                    SpendRepo(session), self.tracker, campaign_id, yesterday, campaign.cpm
                )
                budget = campaign.daily_budget
                amount = carryover_for(budget, yesterday_spent, budget)
                row = Carryover(campaign_id=campaign_id, day=today, amount=amount)
                try:
                    return carryover_repo.add(row)
                except IntegrityError:
                    session.rollback()
                    existing = carryover_repo.for_campaign_day(campaign_id, today)
                    if existing is None:
                        raise
                    return existing
            raise NotAllowed(f"campaign {campaign_id} is not active")
