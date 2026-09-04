"""Admin views over the whole order book."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import AdminPrincipal, DbSession, PageParams
from app.api.schemas import AdminOrderPage
from app.db.repositories import CustomerRepository, NotFound, OrderRepository
from app.domain.dates import DateRange
from app.domain.order_state import OrderStatus

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/orders", response_model=AdminOrderPage)
def list_all_orders(
    db: DbSession,
    _admin: AdminPrincipal,
    page: PageParams,
    status_filter: Annotated[OrderStatus | None, Query(alias="status")] = None,
    customer_id: Annotated[int | None, Query()] = None,
    created_since: Annotated[date | None, Query()] = None,
) -> dict[str, object]:
    """List orders across every customer, newest first.

    Returns 200 with the page of orders. Support passes ``status`` to look at
    one stage of the lifecycle, ``customer_id`` for a single customer, and
    ``created_since`` for a calendar day cutoff read as UTC midnight.
    """
    if customer_id is not None:
        try:
            CustomerRepository(db).get(customer_id)
        except NotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "customer not found") from exc

    since = datetime.combine(created_since, time.min, tzinfo=UTC) if created_since else None
    repo = OrderRepository(db)
    rows = repo.list_all(
        status=status_filter,
        customer_id=customer_id,
        created_since=since,
        limit=page.limit,
        offset=page.offset,
    )
    return {
        "items": rows,
        "total": repo.count_all(status=status_filter, customer_id=customer_id, created_since=since),
        "limit": page.limit,
        "offset": page.offset,
        "max_limit": page.max_limit,
        "item_count": sum(len(o.items) for o in rows),
    }


@router.get("/orders/daily-counts")
def daily_order_counts(
    db: DbSession,
    _admin: AdminPrincipal,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> dict[str, object]:
    """Order counts per day for the trailing window, oldest first."""
    window = DateRange(date.today() - timedelta(days=days - 1), date.today())
    since = datetime.combine(window.start, time.min, tzinfo=UTC)
    counts = OrderRepository(db).daily_counts(since)
    series = [{"day": day.isoformat(), "count": counts.get(day.isoformat(), 0)} for day in window]
    return {"days": series}
