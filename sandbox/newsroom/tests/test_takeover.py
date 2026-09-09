"""Tests for the breaking news takeover feature."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from sandbox.newsroom.db import Article, Section, coerce_utc
from sandbox.newsroom.domain.curation import ArticleStatus, build_home_screen
from sandbox.newsroom.repo import PlacementRepo
from sandbox.newsroom.service import CurationService

EDITOR = "edie@example.com"


def _published(db: Session, headline: str) -> Article:
    article = Article(headline=headline, status=ArticleStatus.DRAFT.value)
    db.add(article)
    db.commit()
    return article


def _placements(session_factory: sessionmaker[Session], section_id: int) -> dict[int, object]:
    session = session_factory()
    try:
        return {p.article_id: p for p in PlacementRepo(session).for_section(section_id)}
    finally:
        session.close()


def test_takeover_shifts_the_existing_slot_one_placement_to_slot_two(
    session_factory: sessionmaker[Session], section: Section, db: Session
):
    service = CurationService(session_factory)
    now = datetime.now(UTC)
    original = _published(db, "Original Lead")
    breaking = _published(db, "Breaking")
    service.publish(EDITOR, original.id, now)
    service.publish(EDITOR, breaking.id, now)
    service.place(EDITOR, section.id, 1, original.id, now, now + timedelta(hours=6), False)

    service.takeover(EDITOR, section.id, breaking.id, 30, now)

    by_article = _placements(session_factory, section.id)
    assert by_article[original.id].slot == 2
    assert by_article[breaking.id].slot == 1


def test_second_takeover_ends_the_first(
    session_factory: sessionmaker[Session], section: Section, db: Session
):
    service = CurationService(session_factory)
    first = _published(db, "First Breaking")
    second = _published(db, "Second Breaking")
    now = datetime.now(UTC)
    service.publish(EDITOR, first.id, now)
    service.publish(EDITOR, second.id, now)

    service.takeover(EDITOR, section.id, first.id, 30, now)
    later = datetime.now(UTC)
    service.takeover(EDITOR, section.id, second.id, 30, later)

    by_article = _placements(session_factory, section.id)
    assert coerce_utc(by_article[first.id].window_end) == later
    assert by_article[second.id].slot == 1


def test_reader_sees_the_takeover_first(
    session_factory: sessionmaker[Session], section: Section, db: Session
):
    service = CurationService(session_factory)
    regular = _published(db, "Regular")
    breaking = _published(db, "Breaking")
    now = datetime.now(UTC)
    service.publish(EDITOR, regular.id, now)
    service.publish(EDITOR, breaking.id, now)
    service.place(EDITOR, section.id, 2, regular.id, now, now + timedelta(hours=6), False)

    service.takeover(EDITOR, section.id, breaking.id, 30, now)

    at = datetime.now(UTC)
    session = session_factory()
    try:
        views = PlacementRepo(session).live_for_section(section.id, at)
    finally:
        session.close()
    ranked = build_home_screen(views, at)
    assert ranked[0].article_id == breaking.id
