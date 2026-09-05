"""Order history export: one page of order history, a cursor for the next
page, and a summary of the whole filtered set. The API endpoint and the
background export task share this builder.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.models import Order
from app.db.repositories import OrderRepository
from app.services.config import get_settings

CURSOR_SEPARATOR = "|"
DEFAULT_WINDOW_DAYS = 90


class InvalidCursor(ValueError):
    """Raised when a cursor did not come from a previous page."""


@dataclass(frozen=True)
class ExportFilters:
    statuses: list[str]
    start: datetime
    end: datetime
    limit: int
    cursor: str | None = None


@dataclass(frozen=True)
class ExportRow:
    order_id: int
    status: str
    created_at: datetime
    total: Decimal
    products: str


@dataclass(frozen=True)
class ExportPage:
    rows: list[ExportRow]
    next_cursor: str | None
    total: int
    orders: int
    gross: Decimal


def window_filters(
    statuses: list[str], days: int, limit: int, cursor: str | None = None
) -> ExportFilters:
    """Build export filters for a request anchored to now, shared by the
    endpoint and the queue handler so the window math lives in one place."""
    end = datetime.now(tz=UTC)
    return ExportFilters(statuses, end - timedelta(days=days), end, limit, cursor)


def parse_cursor(raw: str | None) -> tuple[datetime, int] | None:
    if not raw:
        return None
    seen_at, _, seen_id = raw.partition(CURSOR_SEPARATOR)
    try:
        return datetime.fromisoformat(seen_at), int(seen_id)
    except ValueError as exc:
        raise InvalidCursor(f"cursor {raw!r} is not a page marker") from exc


def _to_row(order: Order) -> ExportRow:
    return ExportRow(
        order_id=order.id,
        status=order.status,
        created_at=order.created_at,
        total=order.total,
        products=", ".join(item.product.name for item in order.items),
    )


def build_export(session: Session, customer_id: int, filters: ExportFilters) -> ExportPage:
    """One page of export rows plus the summary for the whole filter."""
    repo = OrderRepository(session)
    limit = min(filters.limit, get_settings().page_size_max)
    orders = repo.export_page(
        customer_id,
        filters.statuses,
        filters.start,
        filters.end,
        cursor=parse_cursor(filters.cursor),
        limit=limit,
    )
    rows = [_to_row(order) for order in orders]
    count, gross = repo.export_summary(customer_id, filters.statuses, filters.start, filters.end)
    last = orders[-1] if len(orders) == limit else None
    cursor = f"{last.created_at.isoformat()}{CURSOR_SEPARATOR}{last.id}" if last else None
    return ExportPage(
        rows=rows,
        next_cursor=cursor,
        total=repo.count_for_export(customer_id, filters.statuses),
        orders=count,
        gross=gross,
    )
