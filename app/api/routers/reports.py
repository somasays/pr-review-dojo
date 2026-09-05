from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from app.api.deps import AdminPrincipal, DbSession, get_flusher
from app.api.schemas import StatusCount
from app.db.repositories import OrderRepository
from app.domain.order_state import OrderStatus

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


@router.get("/notifications")
def notifications_report(db: DbSession, _admin: AdminPrincipal) -> dict[str, object]:
    """Ops visibility into the flusher: how much mail is queued and how many
    paid orders are still waiting on their confirmation."""
    paid = OrderRepository(db).count_by_status().get(OrderStatus.PAID, 0)
    return {
        "summary": get_flusher().digest(),
        "paid_orders": paid,
    }
