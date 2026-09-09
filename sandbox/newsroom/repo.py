"""Repositories: the only place that builds queries. Bound parameters
only, flush but never commit; the service owns the transaction through
db.unit_of_work()."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from sandbox.newsroom.db import Article, Placement, Section, coerce_utc
from sandbox.newsroom.domain.curation import ArticleStatus, PlacementView


class ArticleRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, article: Article) -> Article:
        self.session.add(article)
        self.session.flush()
        return article

    def get(self, article_id: int) -> Article | None:
        return self.session.get(Article, article_id)


class SectionRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, section_id: int) -> Section | None:
        return self.session.get(Section, section_id)

    def all(self) -> Sequence[Section]:
        return self.session.scalars(select(Section)).all()


class PlacementRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, placement: Placement) -> Placement:
        self.session.add(placement)
        self.session.flush()
        return placement

    def get(self, placement_id: int) -> Placement | None:
        return self.session.get(Placement, placement_id)

    def for_section(self, section_id: int) -> Sequence[Placement]:
        stmt = select(Placement).where(Placement.section_id == section_id)
        return self.session.scalars(stmt).all()

    def for_slot_overlapping(
        self, section_id: int, slot: int, start: datetime, end: datetime
    ) -> Sequence[Placement]:
        """Placements in this section and slot whose half-open window
        [window_start, window_end) overlaps [start, end)."""
        stmt = select(Placement).where(
            Placement.section_id == section_id,
            Placement.slot == slot,
            Placement.window_start < end,
            Placement.window_end > start,
        )
        return self.session.scalars(stmt).all()

    def live_for_section(self, section_id: int, at: datetime) -> list[PlacementView]:
        """Placements for published articles in this section whose window
        covers `at`, as PlacementView rows ready for domain ranking."""
        stmt = (
            select(Placement, Article)
            .join(Article, Article.id == Placement.article_id)
            .where(
                Placement.section_id == section_id,
                Article.status == ArticleStatus.PUBLISHED.value,
                Placement.window_start <= at,
                Placement.window_end > at,
            )
        )
        rows = self.session.execute(stmt).all()
        return [
            PlacementView(
                slot=placement.slot,
                article_id=article.id,
                headline=article.headline,
                published_at=coerce_utc(article.published_at),  # type: ignore[arg-type]
                pinned=placement.pinned,
                boost=article.editorial_boost,
                window_start=coerce_utc(placement.window_start),
                window_end=coerce_utc(placement.window_end),
            )
            for placement, article in rows
        ]
