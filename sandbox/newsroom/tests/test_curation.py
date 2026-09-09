"""Domain-level tests: no IO, no database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sandbox.newsroom.domain.curation import (
    ArticleStatus,
    InvalidTransition,
    is_live,
    rank_key,
    transition,
    windows_overlap,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
T2 = T0 + timedelta(hours=2)


def test_is_live_start_is_inclusive():
    assert is_live(T0, T1, T0) is True


def test_is_live_end_is_exclusive():
    assert is_live(T0, T1, T1) is False


def test_windows_overlap_touching_boundary_does_not_overlap():
    assert windows_overlap(T0, T1, T1, T2) is False


def test_windows_overlap_true_when_windows_intersect():
    assert windows_overlap(T0, T2, T1, T1 + timedelta(hours=1)) is True


def test_rank_key_orders_pinned_first_then_boost_then_newest():
    pinned = rank_key(T0, True, 0)
    boosted = rank_key(T0, False, 5)
    plain_new = rank_key(T1, False, 0)
    plain_old = rank_key(T0, False, 0)
    assert sorted([plain_old, plain_new, boosted, pinned]) == [
        pinned,
        boosted,
        plain_new,
        plain_old,
    ]


def test_transition_draft_to_published_to_retracted():
    assert transition(ArticleStatus.DRAFT, ArticleStatus.PUBLISHED) is ArticleStatus.PUBLISHED
    assert transition(ArticleStatus.PUBLISHED, ArticleStatus.RETRACTED) is ArticleStatus.RETRACTED


def test_transition_invalid_move_raises():
    with pytest.raises(InvalidTransition):
        transition(ArticleStatus.DRAFT, ArticleStatus.RETRACTED)
