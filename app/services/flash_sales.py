"""Flash sale configuration and the in-process purchase counter.

Sales are listed in `ACTIVE_SALES` below. A later change may move this to the
database; for now a handful of sales is easier to review as code.

`SaleCounter` tracks units sold per sale and per customer, shared by every
request thread and by a background thread that closes sales once they end.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from decimal import Decimal

from app.domain.flash_sale import FlashSale
from app.domain.money import Money

log = logging.getLogger(__name__)


class SaleCapExceeded(Exception):
    def __init__(self, sku: str) -> None:
        super().__init__(f"per-customer unit cap reached for {sku}")
        self.sku = sku


ACTIVE_SALES: dict[str, FlashSale] = {
    "FLASHDEAL": FlashSale(
        sku="FLASHDEAL",
        starts_at=datetime(2026, 1, 1, tzinfo=UTC),
        ends_at=datetime(2026, 12, 31, tzinfo=UTC),
        percent_off=Decimal("30"),
        floor_price=Money.of("5.00"),
        per_customer_unit_cap=3,
    ),
}


class SaleCounter:
    """Units sold per sale, and per (sale, customer), kept in memory."""

    def __init__(self, sweep_interval_seconds: float = 30.0) -> None:
        self._lock = threading.Lock()
        self._sold: dict[str, int] = {}
        self._sold_by_customer: dict[tuple[str, int], int] = {}
        self._sweep_interval = sweep_interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="sale-closer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def units_sold(self, sku: str) -> int:
        return self._sold.get(sku, 0)

    def units_sold_by_customer(self, sku: str, customer_id: int) -> int:
        return self._sold_by_customer.get((sku, customer_id), 0)

    def record_purchase(self, sku: str, customer_id: int, quantity: int) -> None:
        with self._lock:
            self._sold[sku] = self._sold.get(sku, 0) + quantity
            key = (sku, customer_id)
            self._sold_by_customer[key] = self._sold_by_customer.get(key, 0) + quantity

    def reset(self, sku: str) -> None:
        """Clear the counters for a sale that has closed."""
        with self._lock:
            self._sold.pop(sku, None)
            for key in [k for k in self._sold_by_customer if k[0] == sku]:
                self._sold_by_customer.pop(key, None)

    def _run(self) -> None:
        while not self._stop_event.wait(self._sweep_interval):
            now = datetime.now(tz=UTC)
            for sku, sale in ACTIVE_SALES.items():
                if now > sale.ends_at:
                    self.reset(sku)
