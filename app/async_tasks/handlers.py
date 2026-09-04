"""Task handlers registered with the queue worker.

`dispatch_confirmations` is the batch entry point: it loads the orders whose
confirmation has not gone out yet and hands the whole list to the notifier in
one call. `record_metric` keeps a small in-process counter so the dispatcher
script can print a summary when it finishes.
"""

from __future__ import annotations

import logging
from typing import Any

from app.async_tasks.worker import QueueWorker
from app.db.repositories import OrderRepository
from app.db.session import session_scope
from app.domain.order_state import OrderStatus
from app.services.notification import BatchNotifier, Message, confirmation_message

log = logging.getLogger(__name__)

BATCH_LIMIT = 100

DISPATCH_KIND = "dispatch_confirmations"
METRIC_KIND = "record_metric"

metrics: list[tuple[str, int]] = []


def build_dispatch_handler(notifier: BatchNotifier) -> Any:
    """Return the handler that drains one batch of pending confirmations."""

    async def dispatch_confirmations(payload: dict[str, Any]) -> None:
        limit = int(payload.get("limit", BATCH_LIMIT))
        with session_scope() as db:
            orders = OrderRepository(db).list_by_status(OrderStatus.PAID, limit=limit)
            messages: list[Message] = [
                confirmation_message(order.customer.email, order.id, str(order.total))
                for order in orders
            ]
        if not messages:
            log.info("no confirmations pending")
            return
        results = await notifier.send_batch(messages)
        log.info("dispatched %d of %d confirmations", len(results), len(messages))

    return dispatch_confirmations


async def record_metric(payload: dict[str, Any]) -> None:
    """Record one counter sample for the dispatcher summary."""
    metrics.append((str(payload["name"]), int(payload["value"])))


def register_handlers(worker: QueueWorker, notifier: BatchNotifier) -> None:
    worker.register(DISPATCH_KIND, build_dispatch_handler(notifier))
    worker.register(METRIC_KIND, record_metric)
