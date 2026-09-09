"""Cache and refresher tests."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from sandbox.newsroom.cache import HomeScreenCache, Refresher
from sandbox.newsroom.db import Article, Section
from sandbox.newsroom.domain.curation import ArticleStatus
from sandbox.newsroom.service import CurationService

EDITOR = "edie@example.com"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_cache_replace_is_atomic_under_concurrent_readers():
    cache = HomeScreenCache()
    cache.replace({1: []})
    barrier = threading.Barrier(6)
    seen_lengths: list[int] = []
    seen_lock = threading.Lock()

    def read() -> None:
        barrier.wait()
        for _ in range(200):
            snapshot = cache.get(1)
            with seen_lock:
                seen_lengths.append(len(snapshot) if snapshot is not None else -1)

    def write() -> None:
        barrier.wait()
        for i in range(200):
            cache.replace({1: [None] * (i % 5)})  # type: ignore[list-item]

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(read) for _ in range(5)] + [pool.submit(write)]
        for f in futures:
            f.result()

    # No reader ever saw a length outside what a fully built snapshot could
    # have (0..4); a torn read would show up as an exception or a bad value.
    assert all(0 <= n <= 4 for n in seen_lengths)


def test_refresher_run_once_builds_a_ranked_snapshot(
    session_factory: sessionmaker[Session], section: Section, db: Session
):
    service = CurationService(session_factory)
    article = Article(headline="Big News", status=ArticleStatus.DRAFT.value)
    db.add(article)
    db.commit()
    service.publish(EDITOR, article.id, T0)
    service.place(EDITOR, section.id, 1, article.id, T0, T0 + timedelta(hours=2), False)

    cache = HomeScreenCache()
    refresher = Refresher(session_factory, cache, interval_seconds=9999)
    refresher.run_once(T0)

    snapshot = cache.get(section.id)
    assert snapshot is not None
    assert [p.article_id for p in snapshot] == [article.id]
