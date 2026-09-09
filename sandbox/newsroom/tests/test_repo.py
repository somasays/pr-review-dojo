"""Repository-level tests against an in-memory SQLite database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from sandbox.newsroom.db import Article, Placement, Section
from sandbox.newsroom.domain.curation import ArticleStatus
from sandbox.newsroom.repo import ArticleRepo, PlacementRepo

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
T2 = T0 + timedelta(hours=2)


def _published_article(db: Session, headline: str = "Headline") -> Article:
    article = Article(headline=headline, status=ArticleStatus.PUBLISHED.value, published_at=T0)
    db.add(article)
    db.commit()
    return article


def test_for_slot_overlapping_finds_only_the_same_slot_and_overlapping_window(
    db: Session, section: Section
):
    article = _published_article(db)
    placement = Placement(
        section_id=section.id,
        slot=1,
        article_id=article.id,
        window_start=T0,
        window_end=T2,
        pinned=False,
        created_by="edie@example.com",
    )
    db.add(placement)
    db.commit()

    repo = PlacementRepo(db)
    assert len(repo.for_slot_overlapping(section.id, 1, T1, T1 + timedelta(hours=1))) == 1
    assert len(repo.for_slot_overlapping(section.id, 1, T2, T2 + timedelta(hours=1))) == 0
    assert len(repo.for_slot_overlapping(section.id, 2, T0, T2)) == 0


def test_live_for_section_excludes_unpublished_and_out_of_window(db: Session, section: Section):
    live_article = _published_article(db, "Live")
    draft_article = Article(headline="Draft", status=ArticleStatus.DRAFT.value)
    db.add(draft_article)
    db.commit()

    db.add_all(
        [
            Placement(
                section_id=section.id,
                slot=1,
                article_id=live_article.id,
                window_start=T0,
                window_end=T2,
                pinned=False,
                created_by="edie@example.com",
            ),
            Placement(
                section_id=section.id,
                slot=2,
                article_id=draft_article.id,
                window_start=T0,
                window_end=T2,
                pinned=False,
                created_by="edie@example.com",
            ),
        ]
    )
    db.commit()

    views = PlacementRepo(db).live_for_section(section.id, T1)
    assert [v.article_id for v in views] == [live_article.id]


def test_article_repo_add_then_get(db: Session):
    article = ArticleRepo(db).add(Article(headline="New", status=ArticleStatus.DRAFT.value))
    db.commit()
    assert ArticleRepo(db).get(article.id).headline == "New"
