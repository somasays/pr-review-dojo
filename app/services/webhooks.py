"""Outbound webhooks.

A subscriber registers an HTTPS endpoint and receives one JSON body per order
event. The dispatcher fans one event out to every endpoint, caps each attempt
with the configured timeout, retries the statuses the gateway documents as
transient, and never sends the same event to the same endpoint twice. Its
semaphore is the process-wide cap on concurrent posts, so one dispatcher is
built per process and shared by every handler that needs one.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.services.config import Settings, get_settings

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class WebhookEndpoint:
    url: str


@dataclass(frozen=True)
class WebhookEvent:
    id: str
    kind: str
    data: dict[str, Any]

    def body(self) -> dict[str, Any]:
        return {"event_id": self.id, "kind": self.kind, "data": self.data}


@dataclass(frozen=True)
class DeliveryResult:
    url: str
    ok: bool
    status: int | None = None
    error: str | None = None


class Transport(Protocol):
    async def post(self, url: str, body: dict[str, Any]) -> int: ...


class HttpxTransport:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def post(self, url: str, body: dict[str, Any]) -> int:
        response = await self.client.post(url, json=body)
        return response.status_code


class WebhookDispatcher:
    def __init__(self, transport: Transport, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        self.transport = transport
        self.attempts = settings.webhook_attempts
        self.timeout = settings.webhook_timeout_ms
        self.backoff_seconds = settings.notify_backoff_seconds
        self.sem = asyncio.Semaphore(settings.webhook_max_parallel)
        self._delivered: set[str] = set()

    @staticmethod
    def _key(endpoint: WebhookEndpoint, event: WebhookEvent) -> str:
        return f"{endpoint.url}|{event.id}"

    async def deliver(self, endpoint: WebhookEndpoint, event: WebhookEvent) -> DeliveryResult:
        key = self._key(endpoint, event)
        if key in self._delivered:
            return DeliveryResult(endpoint.url, ok=True)
        async with self.sem:
            result = await self._post(endpoint, event)
        if result.ok:
            self._delivered.add(key)
        return result

    async def _post(self, endpoint: WebhookEndpoint, event: WebhookEvent) -> DeliveryResult:
        last = DeliveryResult(endpoint.url, ok=False, error="not attempted")
        for attempt in range(1, self.attempts + 1):
            if attempt > 1:
                await asyncio.sleep(self.backoff_seconds * (attempt - 1))
            try:
                status = await asyncio.wait_for(
                    self.transport.post(endpoint.url, event.body()), timeout=self.timeout
                )
            except TimeoutError:
                last = DeliveryResult(endpoint.url, ok=False, error="timeout")
                continue
            except Exception as exc:
                log.warning("attempt %d to %s failed: %s", attempt, endpoint.url, exc)
                last = DeliveryResult(endpoint.url, ok=False, error=str(exc))
                continue
            if status < 400:
                return DeliveryResult(endpoint.url, ok=True, status=status)
            last = DeliveryResult(endpoint.url, ok=False, status=status, error=f"HTTP {status}")
            if status not in RETRYABLE_STATUS:
                break
        return last

    async def fan_out(
        self, event: WebhookEvent, endpoints: list[WebhookEndpoint]
    ) -> list[DeliveryResult]:
        futs = [asyncio.ensure_future(self.deliver(e, event)) for e in endpoints]
        return await asyncio.gather(*futs)


def is_retryable(result: DeliveryResult) -> bool:
    """True when the endpoint asked us to come back later."""
    if result.ok:
        return False
    if result.status is not None:
        return result.status in RETRYABLE_STATUS
    return result.error == "timeout"
