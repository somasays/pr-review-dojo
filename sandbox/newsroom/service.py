"""Publish, place, and retract: business rules layered on the
repositories. Each public method opens exactly one unit of work."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from sandbox.newsroom.db import Article, Placement, ensure_aware_utc, unit_of_work
from sandbox.newsroom.domain.curation import ArticleStatus, InvalidTransition
from sandbox.newsroom.domain.curation import transition as transition_status
from sandbox.newsroom.repo import ArticleRepo, PlacementRepo, SectionRepo


class NotFound(Exception):
    pass


class NotAllowed(Exception):
    pass


class SlotTaken(Exception):
    pass


class CurationService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def publish(self, editor_email: str, article_id: int, now: datetime) -> Article:
        """Move an article from draft to published."""
        ensure_aware_utc(now)
        with unit_of_work(self.session_factory) as session:
            article = ArticleRepo(session).get(article_id)
            if article is None:
                raise NotFound(f"article {article_id} not found")
            try:
                article.status = transition_status(
                    ArticleStatus(article.status), ArticleStatus.PUBLISHED
                ).value
            except InvalidTransition as exc:
                raise NotAllowed(str(exc)) from exc
            article.published_at = now
            session.flush()
            return article

    def place(
        self,
        editor_email: str,
        section_id: int,
        slot: int,
        article_id: int,
        window_start: datetime,
        window_end: datetime,
        pinned: bool,
    ) -> Placement:
        """Place a published article into a slot for a half-open window.
        The article must be published, the slot must exist in the
        section, and no other placement may already hold that slot for an
        overlapping window."""
        ensure_aware_utc(window_start)
        ensure_aware_utc(window_end)
        with unit_of_work(self.session_factory) as session:
            section = SectionRepo(session).get(section_id)
            if section is None:
                raise NotFound(f"section {section_id} not found")
            if not (1 <= slot <= section.slot_count):
                raise NotAllowed(f"slot {slot} is outside section {section_id}'s slot range")

            article = ArticleRepo(session).get(article_id)
            if article is None:
                raise NotFound(f"article {article_id} not found")
            if ArticleStatus(article.status) is not ArticleStatus.PUBLISHED:
                raise NotAllowed(f"article {article_id} is not published")

            placement_repo = PlacementRepo(session)
            if placement_repo.for_slot_overlapping(section_id, slot, window_start, window_end):
                raise SlotTaken(f"slot {slot} in section {section_id} is already taken")

            placement = Placement(
                section_id=section_id,
                slot=slot,
                article_id=article_id,
                window_start=window_start,
                window_end=window_end,
                pinned=pinned,
                created_by=editor_email,
            )
            return placement_repo.add(placement)

    def retract(self, editor_email: str, article_id: int, now: datetime) -> Article:
        """Move a published article to retracted. Existing placements are
        left alone; they simply stop showing up because readers only see
        published articles."""
        ensure_aware_utc(now)
        with unit_of_work(self.session_factory) as session:
            article = ArticleRepo(session).get(article_id)
            if article is None:
                raise NotFound(f"article {article_id} not found")
            try:
                article.status = transition_status(
                    ArticleStatus(article.status), ArticleStatus.RETRACTED
                ).value
            except InvalidTransition as exc:
                raise NotAllowed(str(exc)) from exc
            session.flush()
            return article
