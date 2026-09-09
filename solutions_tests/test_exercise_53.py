"""Hidden tests for exercise 53 (breaking news takeover). Reuses the
sandbox/newsroom/tests fixtures via solutions_tests/conftest.py."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

import sandbox.newsroom.service as service_module
from sandbox.newsroom.cache import HomeScreenCache
from sandbox.newsroom.db import Article, Section, unit_of_work
from sandbox.newsroom.domain.curation import ArticleStatus
from sandbox.newsroom.repo import PlacementRepo
from sandbox.newsroom.service import CurationService
from sandbox.newsroom.tests.conftest import EDITOR_KEY, READER_KEY

EDITOR = "edie@example.com"
NEWSROOM_ROOT = Path(__file__).resolve().parent.parent / "sandbox" / "newsroom"


def _published(db: Session, headline: str) -> Article:
    article = Article(headline=headline, status=ArticleStatus.DRAFT.value)
    db.add(article)
    db.commit()
    return article


def _new_section(session_factory: sessionmaker[Session], slot_count: int) -> Section:
    with unit_of_work(session_factory) as session:
        sec = Section(name="Small", slot_count=slot_count)
        session.add(sec)
        session.flush()
        session.refresh(sec)
        return sec


# 1: takeover must refresh the cache through HomeScreenCache.replace, not a
# direct .snapshot = assignment.


def test_takeover_refreshes_the_cache_through_replace(
    session_factory: sessionmaker[Session], section: Section, db: Session, monkeypatch
):
    calls: list[object] = []
    monkeypatch.setattr(HomeScreenCache, "replace", lambda self, snap: calls.append(snap))

    service = CurationService(session_factory, HomeScreenCache())
    article = _published(db, "Breaking")
    now = datetime.now(UTC)
    service.publish(EDITOR, article.id, now)

    service.takeover(EDITOR, section.id, article.id, 30, now)

    assert calls, "takeover never called HomeScreenCache.replace"


def test_no_direct_snapshot_assignment_outside_cache_module():
    for path in NEWSROOM_ROOT.rglob("*.py"):
        if path.name == "cache.py":
            continue
        text = path.read_text()
        assert ".snapshot = " not in text, f"{path} assigns .snapshot directly"


# 2: the reader-facing snapshot must contain the takeover immediately after
# takeover() returns, not only after the next scheduled refresh.


def test_reader_snapshot_contains_the_takeover_immediately(
    session_factory: sessionmaker[Session], section: Section, db: Session
):
    cache = HomeScreenCache()
    service = CurationService(session_factory, cache)
    article = _published(db, "Breaking")
    now = datetime.now(UTC)
    service.publish(EDITOR, article.id, now)

    service.takeover(EDITOR, section.id, article.id, 30, now)

    snapshot = cache.get(section.id)
    assert snapshot is not None
    assert any(p.article_id == article.id for p in snapshot)


# 3 / 8: a placement that would be shifted past the section's last slot is
# dropped, not moved to a slot that does not exist.


def test_takeover_does_not_move_a_dropped_placement_past_the_last_slot(
    session_factory: sessionmaker[Session], db: Session
):
    section = _new_section(session_factory, slot_count=2)
    a1 = _published(db, "A1")
    a2 = _published(db, "A2")
    breaking = _published(db, "Breaking")
    service = CurationService(session_factory)
    now = datetime.now(UTC)
    for article in (a1, a2, breaking):
        service.publish(EDITOR, article.id, now)
    service.place(EDITOR, section.id, 1, a1.id, now, now + timedelta(hours=1), False)
    service.place(EDITOR, section.id, 2, a2.id, now, now + timedelta(hours=1), False)

    service.takeover(EDITOR, section.id, breaking.id, 30, now)

    fresh = session_factory()
    try:
        placements = PlacementRepo(fresh).for_section(section.id)
    finally:
        fresh.close()
    assert all(p.slot <= section.slot_count for p in placements)


# 8: the takeover is no longer live once its window ends.


def test_takeover_is_not_live_at_its_window_end_boundary(
    session_factory: sessionmaker[Session], section: Section, db: Session
):
    service = CurationService(session_factory)
    article = _published(db, "Breaking")
    now = datetime.now(UTC)
    service.publish(EDITOR, article.id, now)
    service.takeover(EDITOR, section.id, article.id, 30, now)

    fresh = session_factory()
    try:
        just_before = PlacementRepo(fresh).live_for_section(
            section.id, now + timedelta(minutes=29, seconds=59)
        )
        at_end = PlacementRepo(fresh).live_for_section(section.id, now + timedelta(minutes=30))
    finally:
        fresh.close()
    assert any(v.article_id == article.id for v in just_before)
    assert all(v.article_id != article.id for v in at_end)


# 4: readers never see who created a placement.


def test_home_screen_response_never_includes_created_by(client: TestClient, section: Section):
    from sandbox.newsroom import api

    session = api.app.state.refresher.session_factory()
    try:
        article = Article(headline="Breaking", status=ArticleStatus.DRAFT.value)
        session.add(article)
        session.commit()
        article_id = article.id
    finally:
        session.close()

    now = datetime.now(UTC)
    client.post(
        f"/articles/{article_id}/publish",
        json={"now": now.isoformat()},
        headers={"X-Newsroom-Key": EDITOR_KEY},
    )
    client.post(
        f"/sections/{section.id}/takeover",
        json={"article_id": article_id, "minutes": 30},
        headers={"X-Newsroom-Key": EDITOR_KEY},
    )
    api.app.state.refresher.run_once(datetime.now(UTC))

    response = client.get(f"/home/{section.id}", headers={"X-Newsroom-Key": READER_KEY})
    assert response.status_code == 200
    body = response.json()
    assert body
    assert all("created_by" not in item for item in body)


# 5: the default takeover length comes from settings passed at construction,
# not an environment read inside the method.


def test_service_module_does_not_read_environ_directly():
    text = (NEWSROOM_ROOT / "service.py").read_text()
    assert "environ" not in text


# 6: takeover uses the `now` it was given, not a fresh wall-clock read.


def test_takeover_does_not_call_datetime_now_directly():
    source = inspect.getsource(CurationService.takeover)
    assert "datetime.now(" not in source


# 7: place and takeover share one slot-overlap check.


def test_place_and_takeover_share_one_overlap_helper():
    helper = getattr(service_module, "_ensure_slot_free", None)
    assert helper is not None, "expected a shared slot-overlap helper in service.py"
    place_source = inspect.getsource(CurationService.place)
    takeover_source = inspect.getsource(CurationService.takeover)
    assert helper.__name__ in place_source
    assert helper.__name__ in takeover_source
