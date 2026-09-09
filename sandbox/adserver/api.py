"""FastAPI app for the adserver service. Auth is X-Adserver-Key:
ADSERVER_KEYS is a comma-separated list of "role:email:key" triples, role
being ops or advertiser. The tracker and flusher are created once per app
in `create_app()` and started/stopped by the lifespan."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from os import environ
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, sessionmaker

from sandbox.adserver.db import Campaign, Carryover, get_session_factory
from sandbox.adserver.domain.pacing import effective_budget
from sandbox.adserver.repo import CarryoverRepo, SpendRepo
from sandbox.adserver.service import CampaignService, NotAllowed, NotFound, ServingService
from sandbox.adserver.tracker import Flusher, SpendTracker

FLUSH_INTERVAL_SECONDS = 30.0


def _keys() -> dict[str, tuple[str, str]]:
    """Parse ADSERVER_KEYS into {key: (role, email)}."""
    raw = environ.get("ADSERVER_KEYS", "")
    keys: dict[str, tuple[str, str]] = {}
    for triple in raw.split(","):
        role, _, rest = triple.strip().partition(":")
        email, _, key = rest.partition(":")
        if role and email and key:
            keys[key] = (role, email)
    return keys


def get_identity(x_adserver_key: Annotated[str | None, Header()] = None) -> tuple[str, str]:
    """The (role, email) for a valid X-Adserver-Key, of either role."""
    if not x_adserver_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-Adserver-Key")
    identity = _keys().get(x_adserver_key)
    if identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid X-Adserver-Key")
    return identity


Identity = Annotated[tuple[str, str], Depends(get_identity)]


def require_ops(identity: Identity) -> str:
    role, email = identity
    if role != "ops":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "an ops key is required")
    return email


OpsEmail = Annotated[str, Depends(require_ops)]
ServiceSessionFactory = Annotated[sessionmaker[Session], Depends(get_session_factory)]


def get_campaign_service(
    request: Request, session_factory: ServiceSessionFactory
) -> CampaignService:
    tracker: SpendTracker = request.app.state.tracker
    return CampaignService(session_factory, tracker)


CampaignServiceDep = Annotated[CampaignService, Depends(get_campaign_service)]


def get_serving_service(request: Request, session_factory: ServiceSessionFactory) -> ServingService:
    tracker: SpendTracker = request.app.state.tracker
    return ServingService(session_factory, tracker)


ServingServiceDep = Annotated[ServingService, Depends(get_serving_service)]


class CampaignIn(BaseModel):
    advertiser_email: str
    name: str
    daily_budget: Decimal
    cpm: Decimal
    carryover_enabled: bool = False


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    advertiser_id: int
    name: str
    daily_budget: Decimal
    cpm: Decimal
    status: str


class ServeOut(BaseModel):
    serve: bool


class SpendDayOut(BaseModel):
    day: str
    impressions: int
    amount: Decimal


class SpendOut(BaseModel):
    campaign_id: int
    days: list[SpendDayOut]
    tracked_today: Decimal
    effective_budget_today: Decimal


class CarryoverOut(BaseModel):
    campaign_id: int
    day: str
    amount: Decimal


def create_app() -> FastAPI:
    tracker = SpendTracker()
    flusher = Flusher(get_session_factory(), tracker, FLUSH_INTERVAL_SECONDS)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        flusher.start()
        try:
            yield
        finally:
            flusher.stop()

    app = FastAPI(title="Adserver", version="0.1.0", lifespan=lifespan)
    app.state.tracker = tracker
    app.state.flusher = flusher

    @app.exception_handler(NotFound)
    async def not_found(_request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)})

    @app.exception_handler(NotAllowed)
    async def not_allowed(_request: Request, exc: NotAllowed) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def bad_value(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": str(exc)}
        )

    @app.post("/campaigns", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
    def create_campaign(body: CampaignIn, ops: OpsEmail, service: CampaignServiceDep) -> Campaign:
        return service.create(
            ops,
            body.advertiser_email,
            body.name,
            body.daily_budget,
            body.cpm,
            body.carryover_enabled,
        )

    @app.post("/campaigns/{campaign_id}/pause", response_model=CampaignOut)
    def pause_campaign(campaign_id: int, ops: OpsEmail, service: CampaignServiceDep) -> Campaign:
        return service.pause(ops, campaign_id)

    @app.post("/campaigns/{campaign_id}/resume", response_model=CampaignOut)
    def resume_campaign(campaign_id: int, ops: OpsEmail, service: CampaignServiceDep) -> Campaign:
        return service.resume(ops, campaign_id)

    @app.post("/campaigns/{campaign_id}/end", response_model=CampaignOut)
    def end_campaign(campaign_id: int, ops: OpsEmail, service: CampaignServiceDep) -> Campaign:
        return service.end(ops, campaign_id)

    @app.post("/campaigns/{campaign_id}/carryover", response_model=CarryoverOut)
    def apply_carryover(
        campaign_id: int, identity: Identity, service: CampaignServiceDep
    ) -> Carryover:
        _role, email = identity
        return service.apply_carryover(email, campaign_id, datetime.now(UTC).date())

    @app.post("/serve/{campaign_id}", response_model=ServeOut)
    def serve(campaign_id: int, _ops: OpsEmail, service: ServingServiceDep) -> ServeOut:
        return ServeOut(serve=service.decide(campaign_id, datetime.now(UTC)))

    @app.get("/campaigns/{campaign_id}/spend", response_model=SpendOut)
    def get_spend(
        campaign_id: int,
        identity: Identity,
        session_factory: ServiceSessionFactory,
        request: Request,
    ) -> SpendOut:
        role, email = identity
        session = session_factory()
        try:
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                raise NotFound(f"campaign {campaign_id} not found")
            if role != "ops" and campaign.advertiser.email != email:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "not your campaign")
            today = datetime.now(UTC).date()
            spend_repo = SpendRepo(session)
            rows = spend_repo.for_campaign(campaign_id)
            today_row = spend_repo.for_campaign_day(campaign_id, today)
            flushed_today = today_row.amount if today_row is not None else Decimal("0.00")
            tracker: SpendTracker = request.app.state.tracker
            tracked_today = tracker.spent_today(campaign_id, today, campaign.cpm, flushed_today)
            carryover_row = CarryoverRepo(session).for_campaign_day(campaign_id, today)
            carryover_amount = (
                carryover_row.amount if carryover_row is not None else Decimal("0.00")
            )
            return SpendOut(
                campaign_id=campaign_id,
                days=[
                    SpendDayOut(day=r.day.isoformat(), impressions=r.impressions, amount=r.amount)
                    for r in rows
                ],
                tracked_today=tracked_today,
                effective_budget_today=effective_budget(campaign.daily_budget, carryover_amount),
            )
        finally:
            session.close()

    return app


app = create_app()
