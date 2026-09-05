"""In-process stock reservation holds.

A hold keeps units out of reach of other checkouts while a customer finishes
paying. Holds expire after ten minutes; a background sweep thread drops the
expired ones and, when a snapshot path is configured, writes the outstanding
holds to disk so a restart does not forget them. The cache is process wide, so
every request thread and the sweep thread share one instance.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.domain.dates import utcnow

log = logging.getLogger(__name__)

HOLD_TTL = timedelta(minutes=10)
SWEEP_SECONDS = 30.0


@dataclass(frozen=True)
class Hold:
    token: str
    sku: str
    quantity: int
    expires_at: datetime


def format_snapshot(holds: dict[str, Hold]) -> dict[str, list[object]]:
    """Plain, JSON-ready shape for the holds map. No IO, no session."""
    return {
        token: [hold.sku, hold.quantity, hold.expires_at.isoformat()]
        for token, hold in holds.items()
    }


class ReservationCache:
    """Holds stock for a short window while a checkout completes."""

    def __init__(
        self,
        path: Path | None = None,
        ttl: timedelta = HOLD_TTL,
        sweep_seconds: float = SWEEP_SECONDS,
    ) -> None:
        self.path = path
        self.ttl = ttl
        self.sweep_seconds = sweep_seconds
        self.recently_expired: deque[str] = deque(maxlen=200)
        self._held: dict[str, int] = {}
        self._holds: dict[str, Hold] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="reservation-sweep", daemon=False)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self.persist()

    def _run(self) -> None:
        while not self._stop.wait(self.sweep_seconds):
            try:
                self.expire()
                self.persist()
            except Exception:
                # One bad round must not kill the sweep for the life of the process.
                log.exception("reservation sweep failed")

    def available(self, sku: str, stock: int) -> int:
        with self._lock:
            return self._available_locked(sku, stock)

    def _available_locked(self, sku: str, stock: int) -> int:
        return stock - self._held.get(sku, 0)

    def held_skus(self) -> list[str]:
        with self._lock:
            return sorted(sku for sku, qty in self._held.items() if qty > 0)

    def reserve(
        self, sku: str, quantity: int, stock: int, now: datetime | None = None
    ) -> Hold | None:
        """Hold `quantity` units of `sku`, or return None when they are gone."""
        hold = Hold(
            token=uuid4().hex,
            sku=sku,
            quantity=quantity,
            expires_at=(now or utcnow()) + self.ttl,
        )
        with self._lock:
            if self._available_locked(sku, stock) < quantity:
                return None
            self._held[sku] = self._held.get(sku, 0) + quantity
            self._holds[hold.token] = hold
        return hold

    def release(self, token: str) -> bool:
        with self._lock:
            hold = self._holds.pop(token, None)
            if hold is None:
                return False
            self._drop_locked(hold)
        return True

    def expire(self, now: datetime | None = None) -> int:
        now = now or utcnow()
        with self._lock:
            stale = [hold for hold in self._holds.values() if hold.expires_at <= now]
            for hold in stale:
                del self._holds[hold.token]
                self._drop_locked(hold)
        for hold in stale:
            self.recently_expired.append(hold.sku)
        return len(stale)

    def _drop_locked(self, hold: Hold) -> None:
        remaining = self._held.get(hold.sku, 0) - hold.quantity
        if remaining > 0:
            self._held[hold.sku] = remaining
        else:
            self._held.pop(hold.sku, None)

    def persist(self) -> None:
        if self.path is None:
            return
        with self._lock:
            snapshot = format_snapshot(self._holds)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        tmp = Path(raw_tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(snapshot, fh)
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        holds = {
            token: Hold(token, sku, quantity, datetime.fromisoformat(expires_at))
            for token, (sku, quantity, expires_at) in raw.items()
        }
        held: dict[str, int] = {}
        for hold in holds.values():
            held[hold.sku] = held.get(hold.sku, 0) + hold.quantity
        with self._lock:
            self._holds = holds
            self._held = held
        log.info("restored %d reservation holds from %s", len(holds), self.path)
