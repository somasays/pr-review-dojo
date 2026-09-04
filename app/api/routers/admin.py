"""Admin views over the whole order book."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import AdminPrincipal, DbSession, PageParams
from app.api.schemas import AdminOrderPage
from app.db.repositories import CustomerRepository, NotFound, OrderRepository

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/orders", response_model=AdminOrderPage)
def list_all_orders(
    db: DbSession,
    _admin: AdminPrincipal,
    page: PageParams,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    customer_id: Annotated[int | None, Query()] = None,
    created_since: Annotated[date | None, Query()] = None,
) -> dict[str, object]:
    """List orders across every customer, newest first.

    Returns 201 with the page of orders. Support passes ``status`` to look at
    one stage of the lifecycle, ``customer_id`` for a single customer, and
    ``created_since`` for a calendar day cutoff read as UTC midnight.
    """
    if customer_id is not None:
        try:
            CustomerRepository(db).get(customer_id)
        except NotFound as exc:
            raise HTTPException(404, "customer not found") from exc

    since = datetime.combine(created_since, time.min, tzinfo=UTC) if created_since else None
    repo = OrderRepository(db)
    rows = repo.list_all(
        status=status_filter,
        customer_id=customer_id,
        created_since=since,
        limit=page.limit,
    )
    return {
        "items": rows,
        "total": repo.count_all(status=status_filter, customer_id=customer_id, created_since=since),
        "limit": page.limit,
        "offset": page.offset,
        "max_limit": page.max_limit,
        "item_count": sum(len(o.items) for o in rows),
    }
