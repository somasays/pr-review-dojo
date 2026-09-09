"""Pure logic with no IO: article status transitions, window and overlap
math, and the home screen ranking. See sandbox/newsroom/README.md for the
conventions this module follows."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum


class ArticleStatus(Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RETRACTED = "retracted"


class InvalidTransition(Exception):
    pass


_ALLOWED = {
    ArticleStatus.DRAFT: ArticleStatus.PUBLISHED,
    ArticleStatus.PUBLISHED: ArticleStatus.RETRACTED,
}


def transition(current: ArticleStatus, target: ArticleStatus) -> ArticleStatus:
    """The only function allowed to move an article's status. Raises
    InvalidTransition for any move other than draft -> published ->
    retracted."""
    if _ALLOWED.get(current) is not target:
        raise InvalidTransition(f"cannot move an article from {current.value} to {target.value}")
    return target


def is_live(window_start: datetime, window_end: datetime, at: datetime) -> bool:
    """True for `at` inside the half-open window [window_start, window_end)."""
    return window_start <= at < window_end


def windows_overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """True when two half-open windows [a_start, a_end) and [b_start, b_end)
    share any instant."""
    return a_start < b_end and b_start < a_end


def rank_key(published_at: datetime, pinned: bool, editorial_boost: int) -> tuple[int, int, float]:
    """Sortable ascending: pinned first, then boost descending, then newest
    first."""
    return (0 if pinned else 1, -editorial_boost, -published_at.timestamp())


@dataclass(frozen=True)
class PlacementView:
    slot: int
    article_id: int
    headline: str
    published_at: datetime
    pinned: bool
    boost: int
    window_start: datetime
    window_end: datetime
    created_by: str


def build_home_screen(placements: Sequence[PlacementView], at: datetime) -> list[PlacementView]:
    """Filter to placements live at `at`, ordered by rank_key."""
    live = [p for p in placements if is_live(p.window_start, p.window_end, at)]
    live.sort(key=lambda p: rank_key(p.published_at, p.pinned, p.boost))
    return live


def shift_down(
    placements_in_order: Sequence[tuple[int, int]], slot_count: int
) -> list[tuple[int, int]]:
    """placements_in_order: (placement id, current slot), sorted by current
    slot ascending. Each moves one slot down; one whose new slot would fall
    past slot_count is dropped rather than moved."""
    moves = []
    for placement_id, current_slot in placements_in_order:
        new_slot = current_slot + 1
        if new_slot > slot_count + 1:
            continue
        moves.append((placement_id, new_slot))
    return moves


def takeover_window(now: datetime, minutes: int) -> tuple[datetime, datetime]:
    """The half-open window [now, now + minutes) a takeover holds slot 1 for."""
    return now, now + timedelta(minutes=minutes)
