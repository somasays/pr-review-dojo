"""Task handlers registered on the queue worker.

`webhook_fanout` shares one `WebhookDispatcher` across tasks so the cap on
concurrent posts holds, and asks the worker for a delayed retry when every
endpoint answered with something transient.
"""

from __future__ import annotations

import logging
import time
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
ALERT_COOLDOWN_SECONDS = 300.0
DELIVERY_COUNTS: dict[str, int] = {}
_LAST_ALERT: dict[str, float] = {}


def _should_alert(endpoint_url: str) -> bool:
    """One alert per endpoint per cooldown window, so a flapping endpoint
    does not page ops on every single attempt."""
    last = _LAST_ALERT.get(endpoint_url)
    now = time.monotonic()
    if last is not None and now - last < ALERT_COOLDOWN_SECONDS:
        return False
    _LAST_ALERT[endpoint_url] = now
    return True


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
        to_alert = [r for r in failed if _should_alert(r.url)]
        if notifications is not None and to_alert:
            await notifications.send_batch([_alert(event, r) for r in to_alert])
        if all(is_retryable(r) for r in failed):
            raise RetryAfter(settings.webhook_retry_after_seconds)
        log.warning("giving up on %s for %d endpoints", event.id, len(failed))

    async def record_delivery_metric(payload: dict[str, Any]) -> None:
        kind = str(payload["kind"])
        DELIVERY_COUNTS[kind] = DELIVERY_COUNTS.get(kind, 0) + int(payload.get("count", 1))

    worker.register("webhook_fanout", webhook_fanout)
    worker.register("record_delivery_metric", record_delivery_metric)
    return dispatcher
