from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AdminPrincipal, DbSession
from app.api.schemas import ActivityOut, PeriodOut, StatusCount, WeekCount
from app.db.repositories import OrderRepository
from app.domain.dates import (
    DateRange,
    business_days,
    coverage,
    next_business_day,
    parse_dt,
    parse_window,
    partition_for,
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


@router.get("/orders/activity", response_model=ActivityOut)
def order_activity(
    db: DbSession,
    _admin: AdminPrincipal,
    days: int = 30,
    include_today: bool = False,
    as_of: datetime | None = None,
    window: Annotated[list[str] | None, Query()] = None,
) -> ActivityOut:
    """Order activity over a trailing window, or over the windows the caller asks for."""
    if window:
        requested = coverage(parse_window(w) for w in window)
    else:
        today = parse_dt(partition_for(as_of)) if as_of else None
        requested = coverage([DateRange.last_n_days(days, today, include_today=include_today)])
    span = requested.span

    rows = OrderRepository(db).created_between(
        datetime.combine(span.start, time.min, tzinfo=UTC),
        datetime.combine(span.end + timedelta(days=1), time.min, tzinfo=UTC),
    )
    order_days = [o.created_at.date() for o in rows]
    active = coverage([DateRange.single(d) for d in order_days])

    return ActivityOut(
        span_start=span.start,
        span_end=span.end,
        requested_days=requested.requested_days,
        covered_days=requested.covered_days,
        duplicate_days=requested.duplicate_days,
        business_days=business_days(span),
        orders=len(rows),
        first_active_day=active.span.start,
        last_active_day=active.span.end,
        active_days=active.covered_days,
        active_periods=[PeriodOut(start=r.start, end=r.end) for r in active.ranges],
        weekly_orders=[
            WeekCount(
                start=week.start,
                end=week.end,
                orders=sum(1 for d in order_days if week.contains(d)),
            )
            for week in span.split_weekly()
        ],
        next_report_day=next_business_day(span.end),
    )
