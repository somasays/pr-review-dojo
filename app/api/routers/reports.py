from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import AdminPrincipal, CurrentPrincipal, DbSession
from app.api.schemas import ExportPageOut, ExportRowOut, StatusCount
from app.db.repositories import OrderRepository
from app.services.export_service import (
    DEFAULT_WINDOW_DAYS,
    ExportFilters,
    InvalidCursor,
    build_export,
)

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/orders/by-status", response_model=list[StatusCount])
def orders_by_status(db: DbSession, _admin: AdminPrincipal) -> list[StatusCount]:
    counts = OrderRepository(db).count_by_status()
    return [StatusCount(status=k, count=v) for k, v in sorted(counts.items())]


@router.get("/orders/recent-total")
def recent_total(db: DbSession, _admin: AdminPrincipal, days: int = 7) -> dict[str, object]:
    end = datetime.now(tz=UTC)
    start = end - timedelta(days=days)
    rows = OrderRepository(db).created_between(start, end)
    total = sum((o.total for o in rows), start=0)
    return {"days": days, "orders": len(rows), "total": str(total)}


@router.get("/orders/export", response_model=ExportPageOut)
def export_order_history(
    db: DbSession,
    principal: CurrentPrincipal,
    status: Annotated[list[str] | None, Query()] = None,
    days: Annotated[int, Query(ge=1, le=365)] = DEFAULT_WINDOW_DAYS,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    cursor: str | None = None,
) -> ExportPageOut:
    end = datetime.now(tz=UTC)
    window_start = end - timedelta(days=days)
    filters = ExportFilters(status or [], window_start, end, limit, cursor)
    try:
        page = build_export(db, principal.customer, filters)
    except InvalidCursor as exc:
        raise HTTPException(400, str(exc)) from exc
    return ExportPageOut(
        items=[ExportRowOut.model_validate(row) for row in page.rows],
        total=page.total,
        next_cursor=page.next_cursor,
        orders=page.orders,
        gross=page.gross,
    )
