"""Task handlers registered on the queue worker.

`webhook_fanout` shares one `WebhookDispatcher` across tasks so the cap on
concurrent posts holds, and asks the worker for a delayed retry when every
endpoint answered with something transient.
"""

from __future__ import annotations

import logging
from typing import Any

from app.async_tasks.worker import QueueWorker, RetryAfter
from app.services.config import Settings, get_settings
from app.services.notification import Message, NotificationService
from app.services.webhooks import (
    DeliveryResult,
    Transport,
    WebhookDispatcher,
    WebhookEndpoint,
    WebhookEvent,
    is_retryable,
)

log = logging.getLogger(__name__)

ALERT_TO = "ops@example.com"
DELIVERY_COUNTS: dict[str, int] = {}


def _alert(event: WebhookEvent, result: DeliveryResult) -> Message:
    return Message(
        to=ALERT_TO,
        subject=f"Webhook delivery failed for {event.kind}",
        body=f"{event.id} could not reach {result.url}: {result.error}",
        dedupe_key=f"webhook-failed:{event.id}:{result.url}",
    )


def build_handlers(
    worker: QueueWorker,
    *,
    transport: Transport,
    endpoints: list[WebhookEndpoint],
    settings: Settings | None = None,
    notifications: NotificationService | None = None,
) -> WebhookDispatcher:
    """Register the webhook handlers on `worker` and return the dispatcher."""
    settings = settings or get_settings()
    dispatcher = WebhookDispatcher(transport, settings)

    async def webhook_fanout(payload: dict[str, Any]) -> None:
        event = WebhookEvent(
            str(payload["event_id"]), str(payload["kind"]), dict(payload.get("data", {}))
        )
        results = await dispatcher.fan_out(event, endpoints)
        failed = [r for r in results if not r.ok]
        if not failed:
            return
        if notifications is not None:
            await notifications.send_batch([_alert(event, r) for r in failed])
        if all(is_retryable(r) for r in failed):
            raise RetryAfter(settings.webhook_retry_after_seconds)
        log.warning("giving up on %s for %d endpoints", event.id, len(failed))

    async def record_delivery_metric(payload: dict[str, Any]) -> None:
        kind = str(payload["kind"])
        DELIVERY_COUNTS[kind] = DELIVERY_COUNTS.get(kind, 0) + int(payload.get("count", 1))

    worker.register("webhook_fanout", webhook_fanout)
    worker.register("record_delivery_metric", record_delivery_metric)
    return dispatcher
