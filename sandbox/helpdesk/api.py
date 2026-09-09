"""FastAPI app for the helpdesk service. Auth is X-Helpdesk-Key:
HELPDESK_KEYS is a comma-separated list of "role:email:key" triples, role
being agent or lead. The metrics instance is created once per app in
`create_app()` and stored on `app.state`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from os import environ
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, sessionmaker

from sandbox.helpdesk.assigner import AutoAssigner
from sandbox.helpdesk.db import Ticket, get_session_factory
from sandbox.helpdesk.domain.sla import Priority
from sandbox.helpdesk.metrics import QueueMetrics
from sandbox.helpdesk.repo import TicketRepo
from sandbox.helpdesk.service import AlreadyClaimed, NotAllowed, NotFound, TicketService

ASSIGNER_INTERVAL_SECONDS = 30


def _keys() -> dict[str, tuple[str, str]]:
    """Parse HELPDESK_KEYS into {key: (role, email)}."""
    raw = environ.get("HELPDESK_KEYS", "")
    keys: dict[str, tuple[str, str]] = {}
    for triple in raw.split(","):
        role, _, rest = triple.strip().partition(":")
        email, _, key = rest.partition(":")
        if role and email and key:
            keys[key] = (role, email)
    return keys


def get_identity(x_helpdesk_key: Annotated[str | None, Header()] = None) -> tuple[str, str]:
    """The (role, email) for a valid X-Helpdesk-Key, of either role."""
    if not x_helpdesk_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-Helpdesk-Key")
    identity = _keys().get(x_helpdesk_key)
    if identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid X-Helpdesk-Key")
    return identity


Identity = Annotated[tuple[str, str], Depends(get_identity)]


def require_agent(identity: Identity) -> str:
    role, email = identity
    if role != "agent":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "an agent key is required")
    return email


def require_lead(identity: Identity) -> str:
    role, email = identity
    if role != "lead":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "a lead key is required")
    return email


AgentEmail = Annotated[str, Depends(require_agent)]
LeadEmail = Annotated[str, Depends(require_lead)]

ServiceSessionFactory = Annotated[sessionmaker[Session], Depends(get_session_factory)]


def get_ticket_service(session_factory: ServiceSessionFactory) -> TicketService:
    return TicketService(session_factory)


TicketServiceDep = Annotated[TicketService, Depends(get_ticket_service)]


def get_metrics(request: Request) -> QueueMetrics:
    metrics: QueueMetrics = request.app.state.metrics
    return metrics


MetricsDep = Annotated[QueueMetrics, Depends(get_metrics)]


class TicketCreateIn(BaseModel):
    subject: str
    priority: str
    now: datetime


class AtIn(BaseModel):
    now: datetime


class AssignIn(BaseModel):
    agent_email: str


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject: str
    priority: str
    status: str
    created_at: datetime
    due_at: datetime
    agent_id: int | None
    claimed_at: datetime | None
    resolved_at: datetime | None


class MetricsOut(BaseModel):
    created: int
    claimed: int
    resolved: int
    assigned: int


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    assigner = AutoAssigner(get_session_factory(), ASSIGNER_INTERVAL_SECONDS, app.state.metrics)
    assigner.start()
    yield
    assigner.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="Helpdesk", version="0.1.0", lifespan=_lifespan)
    app.state.metrics = QueueMetrics()

    @app.exception_handler(NotFound)
    async def not_found(_request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)})

    @app.exception_handler(AlreadyClaimed)
    async def already_claimed(_request: Request, exc: AlreadyClaimed) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})

    @app.exception_handler(NotAllowed)
    async def not_allowed(_request: Request, exc: NotAllowed) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def bad_value(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": str(exc)}
        )

    @app.post("/tickets", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
    def create_ticket(
        body: TicketCreateIn, _identity: Identity, service: TicketServiceDep, metrics: MetricsDep
    ) -> Ticket:
        ticket = service.create(body.subject, Priority(body.priority), body.now)
        metrics.record("created")
        return ticket

    @app.post("/tickets/{ticket_id}/claim", response_model=TicketOut)
    def claim_ticket(
        ticket_id: int,
        body: AtIn,
        agent_email: AgentEmail,
        service: TicketServiceDep,
        metrics: MetricsDep,
    ) -> Ticket:
        ticket = service.claim(agent_email, ticket_id, body.now)
        metrics.record("claimed")
        return ticket

    @app.post("/tickets/{ticket_id}/resolve", response_model=TicketOut)
    def resolve_ticket(
        ticket_id: int,
        body: AtIn,
        identity: Identity,
        service: TicketServiceDep,
        metrics: MetricsDep,
    ) -> Ticket:
        role, email = identity
        ticket = service.resolve(email, ticket_id, body.now, is_lead=role == "lead")
        metrics.record("resolved")
        return ticket

    @app.post("/tickets/{ticket_id}/assign", response_model=TicketOut)
    def assign_ticket(
        ticket_id: int,
        body: AssignIn,
        lead_email: LeadEmail,
        service: TicketServiceDep,
        metrics: MetricsDep,
    ) -> Ticket:
        ticket = service.assign(lead_email, ticket_id, body.agent_email, datetime.now(UTC))
        metrics.record("assigned")
        return ticket

    @app.get("/queue", response_model=list[TicketOut])
    def get_queue(
        _identity: Identity, session_factory: ServiceSessionFactory, limit: int = 50
    ) -> list[Ticket]:
        session = session_factory()
        try:
            return list(TicketRepo(session).unassigned_oldest_first(limit))
        finally:
            session.close()

    @app.get("/metrics", response_model=MetricsOut)
    def get_metrics_endpoint(_lead_email: LeadEmail, metrics: MetricsDep) -> dict[str, int]:
        return metrics.snapshot()

    return app


app = create_app()
