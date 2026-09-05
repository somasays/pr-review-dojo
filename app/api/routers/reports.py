from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from app.api.deps import AdminPrincipal, DbSession
from app.api.schemas import StatusCount
from app.db.repositories import OrderRepository
from app.domain.order_state import OrderStatus
from app.domain.shipping import transit_days

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


@router.get("/orders/shipping-transit")
def shipping_transit(db: DbSession, _admin: AdminPrincipal) -> dict[str, object]:
    rows = OrderRepository(db).list_by_status(OrderStatus.SHIPPED, limit=500)
    today = datetime.now(tz=UTC).date()
    days = [transit_days(o, today) for o in rows if o.shipped_at is not None]
    average = sum(days) / len(days) if days else 0
    return {"orders": len(days), "average_transit_days": average}
