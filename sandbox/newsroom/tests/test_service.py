"""Service-level tests: each public method against an in-memory database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from sandbox.newsroom.db import Article, Section, unit_of_work
from sandbox.newsroom.domain.curation import ArticleStatus
from sandbox.newsroom.repo import ArticleRepo, PlacementRepo
from sandbox.newsroom.service import CurationService, NotAllowed, SlotTaken

EDITOR = "edie@example.com"
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)


@pytest.fixture
def draft_article(db: Session) -> Article:
    article = Article(headline="Big News", status=ArticleStatus.DRAFT.value)
    db.add(article)
    db.commit()
    return article


def test_publish_moves_article_to_published(
    session_factory: sessionmaker[Session], draft_article: Article
):
    service = CurationService(session_factory)
    published = service.publish(EDITOR, draft_article.id, T0)
    assert published.status == ArticleStatus.PUBLISHED.value
    assert published.published_at == T0


def test_place_refused_when_article_is_not_published(
    session_factory: sessionmaker[Session], section: Section, draft_article: Article
):
    service = CurationService(session_factory)
    with pytest.raises(NotAllowed):
        service.place(EDITOR, section.id, 1, draft_article.id, T0, T1, False)


def test_place_refused_outside_the_section_slot_range(
    session_factory: sessionmaker[Session], section: Section, draft_article: Article
):
    service = CurationService(session_factory)
    service.publish(EDITOR, draft_article.id, T0)
    with pytest.raises(NotAllowed):
        service.place(EDITOR, section.id, section.slot_count + 1, draft_article.id, T0, T1, False)


def test_place_refused_on_overlapping_placement_in_the_same_slot(
    session_factory: sessionmaker[Session], section: Section, db: Session
):
    service = CurationService(session_factory)
    first = Article(headline="First", status=ArticleStatus.DRAFT.value)
    second = Article(headline="Second", status=ArticleStatus.DRAFT.value)
    db.add_all([first, second])
    db.commit()
    service.publish(EDITOR, first.id, T0)
    service.publish(EDITOR, second.id, T0)

    service.place(EDITOR, section.id, 1, first.id, T0, T0 + timedelta(hours=2), False)
    with pytest.raises(SlotTaken):
        service.place(EDITOR, section.id, 1, second.id, T1, T1 + timedelta(hours=2), False)


def test_retract_removes_the_article_from_the_next_live_view(
    session_factory: sessionmaker[Session], section: Section, draft_article: Article
):
    service = CurationService(session_factory)
    service.publish(EDITOR, draft_article.id, T0)
    service.place(EDITOR, section.id, 1, draft_article.id, T0, T0 + timedelta(hours=2), False)

    service.retract(EDITOR, draft_article.id, T1)

    with unit_of_work(session_factory) as session:
        views = PlacementRepo(session).live_for_section(section.id, T1)
    assert views == []


def test_unit_of_work_rolls_back_on_error(session_factory: sessionmaker[Session]):
    with pytest.raises(ValueError):
        with unit_of_work(session_factory) as session:
            ArticleRepo(session).add(Article(headline="Doomed", status=ArticleStatus.DRAFT.value))
            raise ValueError("boom")

    with unit_of_work(session_factory) as session:
        assert ArticleRepo(session).get(1) is None
