"""FastAPI app for the newsroom service. Auth is X-Newsroom-Key:
NEWSROOM_KEYS is a comma-separated list of "role:email:key" triples, role
being editor or reader. The cache and refresher are created once per app
in `create_app()` and started/stopped by the lifespan."""

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

from sandbox.newsroom.cache import HomeScreenCache, Refresher
from sandbox.newsroom.db import Article, Placement, get_session_factory
from sandbox.newsroom.repo import PlacementRepo
from sandbox.newsroom.service import CurationService, NotAllowed, NotFound, SlotTaken

REFRESH_INTERVAL_SECONDS = 30.0


def _keys() -> dict[str, tuple[str, str]]:
    """Parse NEWSROOM_KEYS into {key: (role, email)}."""
    raw = environ.get("NEWSROOM_KEYS", "")
    keys: dict[str, tuple[str, str]] = {}
    for triple in raw.split(","):
        role, _, rest = triple.strip().partition(":")
        email, _, key = rest.partition(":")
        if role and email and key:
            keys[key] = (role, email)
    return keys


def get_identity(x_newsroom_key: Annotated[str | None, Header()] = None) -> tuple[str, str]:
    """The (role, email) for a valid X-Newsroom-Key, of either role."""
    if not x_newsroom_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-Newsroom-Key")
    identity = _keys().get(x_newsroom_key)
    if identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid X-Newsroom-Key")
    return identity


Identity = Annotated[tuple[str, str], Depends(get_identity)]


def require_editor(identity: Identity) -> str:
    role, email = identity
    if role != "editor":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "an editor key is required")
    return email


EditorEmail = Annotated[str, Depends(require_editor)]

ServiceSessionFactory = Annotated[sessionmaker[Session], Depends(get_session_factory)]


def get_cache(request: Request) -> HomeScreenCache:
    cache: HomeScreenCache = request.app.state.cache
    return cache


CacheDep = Annotated[HomeScreenCache, Depends(get_cache)]


def get_curation_service(
    session_factory: ServiceSessionFactory, cache: CacheDep
) -> CurationService:
    return CurationService(session_factory, cache)


CurationServiceDep = Annotated[CurationService, Depends(get_curation_service)]


class PublishIn(BaseModel):
    now: datetime


class PlacementIn(BaseModel):
    slot: int
    article_id: int
    window_start: datetime
    window_end: datetime
    pinned: bool = False


class TakeoverIn(BaseModel):
    article_id: int
    minutes: int | None = None


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    headline: str
    status: str
    published_at: datetime | None
    editorial_boost: int


class PlacementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    section_id: int
    slot: int
    article_id: int
    window_start: datetime
    window_end: datetime
    pinned: bool
    created_by: str


class HomeScreenSlotOut(BaseModel):
    slot: int
    article_id: int
    headline: str
    pinned: bool


def create_app() -> FastAPI:
    cache = HomeScreenCache()
    refresher = Refresher(get_session_factory(), cache, REFRESH_INTERVAL_SECONDS)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        refresher.start()
        try:
            yield
        finally:
            refresher.stop()

    app = FastAPI(title="Newsroom", version="0.1.0", lifespan=lifespan)
    app.state.cache = cache
    app.state.refresher = refresher

    @app.exception_handler(NotFound)
    async def not_found(_request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)})

    @app.exception_handler(SlotTaken)
    async def slot_taken(_request: Request, exc: SlotTaken) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})

    @app.exception_handler(NotAllowed)
    async def not_allowed(_request: Request, exc: NotAllowed) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def bad_value(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": str(exc)}
        )

    @app.post("/articles/{article_id}/publish", response_model=ArticleOut)
    def publish_article(
        article_id: int, body: PublishIn, editor_email: EditorEmail, service: CurationServiceDep
    ) -> Article:
        return service.publish(editor_email, article_id, body.now)

    @app.post(
        "/sections/{section_id}/placements",
        response_model=PlacementOut,
        status_code=status.HTTP_201_CREATED,
    )
    def place_article(
        section_id: int,
        body: PlacementIn,
        editor_email: EditorEmail,
        service: CurationServiceDep,
    ) -> Placement:
        return service.place(
            editor_email,
            section_id,
            body.slot,
            body.article_id,
            body.window_start,
            body.window_end,
            body.pinned,
        )

    @app.post("/articles/{article_id}/retract", response_model=ArticleOut)
    def retract_article(
        article_id: int, body: PublishIn, editor_email: EditorEmail, service: CurationServiceDep
    ) -> Article:
        return service.retract(editor_email, article_id, body.now)

    @app.get("/home/{section_id}", response_model=list[HomeScreenSlotOut])
    def get_home_screen(
        section_id: int, _identity: Identity, cache: CacheDep
    ) -> list[HomeScreenSlotOut]:
        placements = cache.get(section_id)
        if placements is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "home screen not ready yet")
        return [
            HomeScreenSlotOut(
                slot=p.slot, article_id=p.article_id, headline=p.headline, pinned=p.pinned
            )
            for p in placements
        ]

    @app.post(
        "/sections/{section_id}/takeover",
        response_model=PlacementOut,
        status_code=status.HTTP_201_CREATED,
    )
    def takeover_section(
        section_id: int, body: TakeoverIn, editor_email: EditorEmail, service: CurationServiceDep
    ) -> Placement:
        now = datetime.now(UTC)
        return service.takeover(editor_email, section_id, body.article_id, body.minutes, now)

    @app.get("/sections/{section_id}/placements", response_model=list[PlacementOut])
    def list_placements(
        section_id: int, _editor_email: EditorEmail, session_factory: ServiceSessionFactory
    ) -> list[Placement]:
        session = session_factory()
        try:
            return list(PlacementRepo(session).for_section(section_id))
        finally:
            session.close()

    return app


app = create_app()
