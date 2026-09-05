from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from app.api.deps import AdminPrincipal, DbSession, Reservations
from app.api.schemas import StatusCount
from app.db.repositories import OrderRepository
from app.services.reservations import hold_metrics

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


@router.get("/reservations")
def reservation_holds(_admin: AdminPrincipal, cache: Reservations) -> dict[str, object]:
    """What the reservation cache is currently holding back from checkout."""
    skus = tuple(cache.held_skus())
    return {
        "metrics": hold_metrics(),
        "held": cache.held_many(skus),
        "recently_expired": list(cache.recently_expired),
    }
