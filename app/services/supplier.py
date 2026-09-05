"""Supplier inventory gateway.

The supplier exposes one inventory endpoint per SKU. The client is injected
so tests and local development can run against the in-memory implementation
instead of the real gateway.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

import httpx

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SupplierStock:
    """One inventory level reported by the supplier."""

    sku: str
    quantity: int


class SupplierClient(Protocol):
    async def fetch(self, sku: str) -> SupplierStock | None: ...


@dataclass
class InMemorySupplierClient:
    """Records requests and replays canned levels. Used by tests and local runs."""

    levels: dict[str, int] = field(default_factory=dict)
    fail_skus: set[str] = field(default_factory=set)
    requested: list[str] = field(default_factory=list)

    async def fetch(self, sku: str) -> SupplierStock | None:
        self.requested.append(sku)
        if sku in self.fail_skus:
            raise ConnectionError(f"supplier unavailable for {sku}")
        if sku not in self.levels:
            return None
        return SupplierStock(sku=sku, quantity=self.levels[sku])


class HttpSupplierClient:
    """Talks to the supplier over HTTP. One request per SKU."""

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=None)

    async def fetch(self, sku: str) -> SupplierStock | None:
        response = await self.client.get(f"{self.base_url}/inventory/{sku}")
        if response.status_code == 404:
            log.info("supplier does not carry %s", sku)
            return None
        response.raise_for_status()
        body = response.json()
        return SupplierStock(sku=sku, quantity=int(body["quantity"]))
