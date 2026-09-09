"""Publish, place, and retract: business rules layered on the
repositories. Each public method opens exactly one unit of work."""

from __future__ import annotations

from datetime import UTC, datetime
from os import environ

from sqlalchemy.orm import Session, sessionmaker

from sandbox.newsroom.cache import HomeScreenCache
from sandbox.newsroom.db import Article, Placement, coerce_utc, ensure_aware_utc, unit_of_work
from sandbox.newsroom.domain.curation import (
    ArticleStatus,
    InvalidTransition,
    build_home_screen,
    is_live,
    shift_down,
    takeover_window,
)
from sandbox.newsroom.domain.curation import transition as transition_status
from sandbox.newsroom.repo import ArticleRepo, PlacementRepo, SectionRepo


class NotFound(Exception):
    pass


class NotAllowed(Exception):
    pass


class SlotTaken(Exception):
    pass


class CurationService:
    def __init__(
        self, session_factory: sessionmaker[Session], cache: HomeScreenCache | None = None
    ) -> None:
        self.session_factory = session_factory
        self.cache = cache

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

    def takeover(
        self,
        editor_email: str,
        section_id: int,
        article_id: int,
        minutes: int | None,
        now: datetime,
    ) -> Placement:
        """Push a published article into slot 1 for a window, shifting
        whatever is in the way down a slot. A second takeover in the same
        section while one is active replaces it."""
        ensure_aware_utc(now)
        with unit_of_work(self.session_factory) as session:
            section = SectionRepo(session).get(section_id)
            if section is None:
                raise NotFound(f"section {section_id} not found")

            article = ArticleRepo(session).get(article_id)
            if article is None:
                raise NotFound(f"article {article_id} not found")
            if ArticleStatus(article.status) is not ArticleStatus.PUBLISHED:
                raise NotAllowed(f"article {article_id} is not published")

            if minutes is None:
                minutes = int(environ.get("NEWSROOM_DEFAULT_TAKEOVER_MINUTES", "30"))
            window_start, window_end = takeover_window(datetime.now(UTC), minutes)

            placement_repo = PlacementRepo(session)

            # A second takeover during an active one replaces it.
            for existing in placement_repo.for_section(section_id):
                start = coerce_utc(existing.window_start)
                end = coerce_utc(existing.window_end)
                if existing.slot == 1 and existing.pinned and is_live(start, end, now):
                    existing.window_end = now
            session.flush()

            # Shift whatever else is in the way down a slot.
            overlapping = placement_repo.for_section_overlapping(
                section_id, window_start, window_end
            )
            ordered = sorted(((p.id, p.slot) for p in overlapping), key=lambda t: t[1])
            moves = shift_down(ordered, section.slot_count)
            by_id = {p.id: p for p in overlapping}
            for placement_id, new_slot in moves:
                by_id[placement_id].slot = new_slot
            session.flush()

            # Slot 1 must be free before the takeover can go in.
            if placement_repo.for_slot_overlapping(section_id, 1, window_start, window_end):
                raise SlotTaken(f"slot 1 in section {section_id} is already taken")

            # Refresh the home screen immediately instead of waiting for
            # the next scheduled refresh.
            sections = SectionRepo(session).all()
            new_snapshot = {
                s.id: build_home_screen(placement_repo.live_for_section(s.id, now), now)
                for s in sections
            }
            if self.cache is not None:
                self.cache.snapshot = new_snapshot

            placement = Placement(
                section_id=section_id,
                slot=1,
                article_id=article_id,
                window_start=window_start,
                window_end=window_end,
                pinned=True,
                created_by=editor_email,
            )
            return placement_repo.add(placement)
