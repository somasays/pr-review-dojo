"""Task handlers for the queue worker.

Handlers are plain sync callables; the worker runs them in a thread so the
event loop keeps polling.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import sessionmaker

from app.async_tasks.worker import QueueWorker
from app.db.repositories import OrderRepository
from app.db.session import get_engine
from app.domain.order_state import OrderStatus
from app.services.export_service import DEFAULT_WINDOW_DAYS, build_export, window_filters
from app.services.notification import NotificationService

_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)


def export_orders(payload: dict[str, Any]) -> dict[str, object]:
    """Build one page of a customer's order history for the export mailer."""
    filters = window_filters(
        list(payload.get("statuses", [OrderStatus.PAID, OrderStatus.SHIPPED])),
        int(payload.get("days", DEFAULT_WINDOW_DAYS)),
        int(payload.get("limit", 500)),
        payload.get("cursor"),
    )
    with _factory() as session:
        page = build_export(session, int(payload["customer_id"]), filters)
    return {"rows": len(page.rows), "total": page.total, "next_cursor": page.next_cursor}


def notify_export_ready(payload: dict[str, Any], notifications: NotificationService) -> None:
    """Email the customer once their order history export has finished."""
    order_id = int(payload["order_id"])
    with _factory() as session:
        order = OrderRepository(session).get(order_id)
        email = order.customer.email
    notifications.export_ready(email, order_id)


def register(worker: QueueWorker, notifications: NotificationService) -> None:
    worker.register("export_orders", export_orders)
    worker.register(
        "notify_export_ready", lambda payload: notify_export_ready(payload, notifications)
    )
