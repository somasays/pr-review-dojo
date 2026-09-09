"""Models, engine, session factory, and the transaction boundary for the
newsroom service. See sandbox/newsroom/README.md for conventions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from os import environ

from sqlalchemy import Boolean, DateTime, Engine, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from sandbox.newsroom.domain.curation import ArticleStatus


class Base(DeclarativeBase):
    pass


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    headline: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default=ArticleStatus.DRAFT.value
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    editorial_boost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slot_count: Mapped[int] = mapped_column(Integer, nullable=False)


class Placement(Base):
    __tablename__ = "placements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id"), nullable=False)
    slot: Mapped[int] = mapped_column(Integer, nullable=False)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)


def ensure_aware_utc(dt: datetime) -> None:
    """Reject a naive datetime."""
    if dt.tzinfo is None:
        raise ValueError("expected a timezone-aware datetime, got a naive one")


def coerce_utc(dt: datetime) -> datetime:
    """Reattach the UTC tzinfo SQLite drops on a round trip."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _database_url() -> str:
    return environ.get("NEWSROOM_DATABASE_URL", "sqlite://")


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(_database_url(), future=True)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def create_all() -> None:
    Base.metadata.create_all(get_engine())


@contextmanager
def unit_of_work(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Yield a session, commit on success, roll back and re-raise on error.
    The only place a transaction opens or closes."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
