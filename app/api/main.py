"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.deps import get_flusher
from app.api.routers import customers, orders, reports
from app.db.repositories import NotFound

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Run the notification flusher for as long as the app is up."""
    flusher = get_flusher()
    flusher.start()
    try:
        yield
    finally:
        flusher.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="Dojo Orders", version="0.1.0", lifespan=lifespan)
    app.include_router(customers.router)
    app.include_router(orders.router)
    app.include_router(reports.router)

    @app.exception_handler(NotFound)
    async def not_found(_request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
