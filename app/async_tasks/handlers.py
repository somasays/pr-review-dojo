"""Task handlers for the queue worker.

Handlers are plain sync callables; the worker runs them in a thread so the
event loop keeps polling.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import sessionmaker

from app.async_tasks.worker import QueueWorker
from app.db.repositories import OrderRepository
from app.db.session import get_engine, session_scope
from app.domain.order_state import OrderStatus
from app.services.export_service import DEFAULT_WINDOW_DAYS, ExportFilters, build_export
from app.services.notification import InMemorySender, Message

_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
_session = _factory()
_sender = InMemorySender()


def export_orders(payload: dict[str, Any]) -> dict[str, object]:
    """Build one page of a customer's order history for the export mailer."""
    end = datetime.now(tz=UTC)
    filters = ExportFilters(
        statuses=list(payload.get("statuses", [OrderStatus.PAID, OrderStatus.SHIPPED])),
        start=end - timedelta(days=int(payload.get("days", DEFAULT_WINDOW_DAYS))),
        end=end,
        limit=int(payload.get("limit", 500)),
        cursor=payload.get("cursor"),
    )
    page = build_export(_session, int(payload["customer_id"]), filters)
    return {"rows": len(page.rows), "total": page.total, "next_cursor": page.next_cursor}


def notify_export_ready(payload: dict[str, Any], *, dry_run: bool = False) -> None:
    """Email the customer once their order history export has finished."""
    order_id = int(payload["order_id"])
    with session_scope() as session:
        order = OrderRepository(session).get(order_id)
    if dry_run:
        return
    message = Message(
        to=order.customer.email,
        subject="Your order export is ready",
        body=f"Export for order {order.id} is ready to download.",
        dedupe_key=f"export-ready:{order.id}",
    )
    for attempt in range(3):
        try:
            _sender.send(message)
            break
        except ConnectionError:
            if attempt == 2:
                raise


def register(worker: QueueWorker) -> None:
    worker.register("export_orders", export_orders)
    worker.register("notify_export_ready", notify_export_ready)
