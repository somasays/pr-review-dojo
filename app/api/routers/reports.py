from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from app.api.deps import AdminPrincipal, DbSession, get_rate_limiter
from app.api.schemas import StatusCount
from app.db.repositories import OrderRepository
from app.services.config import get_settings
from app.services.rate_limiter import seconds_until_reset

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


def format_rate_limit_row(
    key: str, hits: int, limit: int, resets_in_seconds: int
) -> dict[str, object]:
    """Pure formatting for one row of the rate limit report."""
    return {
        "key": key,
        "hits": hits,
        "resets_in_seconds": resets_in_seconds,
        "usage": f"{hits / limit:.0%}",
    }


@router.get("/rate-limits")
def rate_limit_usage(_admin: AdminPrincipal) -> list[dict[str, object]]:
    limiter = get_rate_limiter()
    settings = get_settings()
    now = time.monotonic()
    rows = []
    for key, hits in limiter.snapshot().items():
        started = limiter.window_started(key)
        resets_in = (
            seconds_until_reset(started, limiter.policy.window_seconds, now) if started else 0
        )
        rows.append(format_rate_limit_row(key, hits, settings.rate_limit_per_minute, resets_in))
    return sorted(rows, key=lambda r: r["key"])
