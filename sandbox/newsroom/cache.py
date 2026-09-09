"""In-process home screen cache and the background thread that keeps it
fresh. See sandbox/newsroom/README.md convention 6: the snapshot is
replaced atomically as one object under the cache's lock, and every
reader takes the same lock."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from sandbox.newsroom.db import unit_of_work
from sandbox.newsroom.domain.curation import PlacementView, build_home_screen
from sandbox.newsroom.repo import PlacementRepo, SectionRepo


class HomeScreenCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.snapshot: dict[int, list[PlacementView]] | None = None

    def get(self, section_id: int) -> list[PlacementView] | None:
        """None means no snapshot has been built yet."""
        with self._lock:
            if self.snapshot is None:
                return None
            return self.snapshot.get(section_id, [])

    def replace(self, new_snapshot: dict[int, list[PlacementView]]) -> None:
        """Swap in a fully built snapshot as one atomic assignment."""
        with self._lock:
            self.snapshot = new_snapshot


class Refresher:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        cache: HomeScreenCache,
        interval_seconds: float,
    ) -> None:
        self.session_factory = session_factory
        self.cache = cache
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self, now: datetime) -> None:
        """Rebuild the snapshot for every section from a fresh session and
        replace it in the cache."""
        with unit_of_work(self.session_factory) as session:
            sections = SectionRepo(session).all()
            placement_repo = PlacementRepo(session)
            new_snapshot = {
                section.id: build_home_screen(placement_repo.live_for_section(section.id, now), now)
                for section in sections
            }
        self.cache.replace(new_snapshot)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.run_once(datetime.now(UTC))

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="newsroom-refresher", daemon=False)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
