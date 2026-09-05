"""Task handlers registered with the queue worker.

`dispatch_confirmations` is the batch entry point: it loads the orders whose
confirmation has not gone out yet and hands the whole list to the notifier in
one call. `record_metric` keeps a small in-process counter so the dispatcher
script can print a summary when it finishes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.async_tasks.worker import QueueWorker
from app.db.repositories import OrderRepository
from app.db.session import session_scope
from app.domain.order_state import OrderStatus
from app.services.notification import (
    AsyncSender,
    BatchNotifier,
    Message,
    confirmation_message,
)

log = logging.getLogger(__name__)

BATCH_LIMIT = 100

DISPATCH_KIND = "dispatch_confirmations"
METRIC_KIND = "record_metric"
GATEWAY_HEALTH_KIND = "check_gateway_health"

metrics: list[tuple[str, int]] = []


def _load_pending_messages(limit: int) -> list[Message]:
    """Read the pending confirmations. Sync: it uses a blocking session."""
    with session_scope() as db:
        orders = OrderRepository(db).list_by_status(OrderStatus.PAID, limit=limit)
        return [
            confirmation_message(order.customer.email, order.id, str(order.total))
            for order in orders
        ]


def build_dispatch_handler(notifier: BatchNotifier) -> Any:
    """Return the handler that drains one batch of pending confirmations."""

    async def dispatch_confirmations(payload: dict[str, Any]) -> None:
        limit = int(payload.get("limit", BATCH_LIMIT))
        messages = await asyncio.to_thread(_load_pending_messages, limit)
        if not messages:
            log.info("no confirmations pending")
            return
        results = await notifier.send_batch(messages)
        log.info("dispatched %d of %d confirmations", len(results), len(messages))

    return dispatch_confirmations


def record_metric(payload: dict[str, Any]) -> None:
    """Record one counter sample for the dispatcher summary."""
    metrics.append((str(payload["name"]), int(payload["value"])))


async def check_gateway_health(payload: dict[str, Any]) -> None:
    """Ping the notification gateway before a dispatch run starts."""
    response = await asyncio.to_thread(httpx.get, str(payload["url"]), timeout=2.0)
    metrics.append(("gateway_status", response.status_code))


async def resend_failed(order_ids: list[int], sender: AsyncSender, *, dry_run: bool = False) -> str:
    """Resend confirmations for orders whose batch send did not go out.

    With dry_run, lists what would be sent without contacting the gateway.
    Returns a small CSV report either way.
    """
    messages = [confirmation_message(f"order{oid}@example.com", oid, "0.00") for oid in order_ids]
    lines = ["order,recipient"]
    if dry_run:
        for message in messages:
            lines.append(f"{message.dedupe_key},{message.to}")
        return "\n".join(lines)
    notifier = BatchNotifier(sender)
    results = await notifier.send_batch(messages)
    for message in results:
        if isinstance(message, Message):
            lines.append(f"{message.dedupe_key},{message.to}")
    return "\n".join(lines)


def register_handlers(worker: QueueWorker, notifier: BatchNotifier) -> None:
    worker.register(DISPATCH_KIND, build_dispatch_handler(notifier))
    worker.register(METRIC_KIND, record_metric)
    worker.register(GATEWAY_HEALTH_KIND, check_gateway_health)
