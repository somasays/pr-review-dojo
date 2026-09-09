"""In-process impression counting and the background flush thread. See
sandbox/adserver/README.md convention 5: one lock guards the tracker."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from sandbox.adserver.db import unit_of_work
from sandbox.adserver.domain.pacing import cost_of
from sandbox.adserver.repo import CampaignRepo, SpendRepo

CampaignDay = tuple[int, date]


class SpendTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counts: dict[CampaignDay, int] = {}
        self._before_read: Callable[[], None] = lambda: None  # test hook, no-op in production

    def record(self, campaign_id: int, day: date) -> None:
        with self._lock:
            key = (campaign_id, day)
            self.counts[key] = self.counts.get(key, 0) + 1

    def count_for(self, campaign_id: int, day: date) -> int:
        """Locked read of the in-memory count for one campaign and day."""
        self._before_read()
        with self._lock:
            return self.counts.get((campaign_id, day), 0)

    def spent_today(self, campaign_id: int, day: date, cpm: Decimal, flushed: Decimal) -> Decimal:
        """The flushed amount plus the in-memory count, priced at cpm."""
        with self._lock:
            count = self.counts.get((campaign_id, day), 0)
        return flushed + cost_of(count, cpm)

    def drain(self) -> dict[CampaignDay, int]:
        """Swap in an empty dict and return the old one. The returned
        dict is no longer shared once swapped, so it is safe to read
        without the lock."""
        with self._lock:
            drained, self.counts = self.counts, {}
        return drained

    def put_back(self, counts: dict[CampaignDay, int]) -> None:
        """Merge counts back in after a failed flush."""
        with self._lock:
            for key, count in counts.items():
                self.counts[key] = self.counts.get(key, 0) + count


class Flusher:
    def __init__(
        self, session_factory: sessionmaker[Session], tracker: SpendTracker, interval_seconds: float
    ) -> None:
        self.session_factory = session_factory
        self.tracker = tracker
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> None:
        """Drain the tracker and upsert the counts as spend rows in one
        unit of work, opening its own session. On failure, put the
        drained counts back so a later flush still counts them."""
        drained = self.tracker.drain()
        if not drained:
            return
        try:
            with unit_of_work(self.session_factory) as session:
                campaign_repo = CampaignRepo(session)
                spend_repo = SpendRepo(session)
                for (campaign_id, day), count in drained.items():
                    campaign = campaign_repo.get(campaign_id)
                    if campaign is None:
                        continue
                    spend_repo.upsert(campaign_id, day, count, cost_of(count, campaign.cpm))
        except Exception:
            self.tracker.put_back(drained)
            raise

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.run_once()
            except Exception:
                pass

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="adserver-flusher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
