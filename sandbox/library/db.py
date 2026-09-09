"""SQLAlchemy 2.x models, engine, session factory, and the transaction
boundary for the library service.

Conventions (see sandbox/library/README.md): integer primary keys, timezone-
aware UTC datetimes (naive datetimes are rejected at the service boundary by
ensure_aware_utc), due dates and other calendar fields are plain `date`, and
nothing is ever hard-deleted. Repositories never commit; `session_scope()`
is the only place a transaction is opened or closed, and only the API's
`get_db` dependency and the overdue job call it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from os import environ

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    Numeric,
    String,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Patron(Base):
    __tablename__ = "patrons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    copies: Mapped[int] = mapped_column(Integer, nullable=False)


class Loan(Base):
    __tablename__ = "loans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False)
    patron_id: Mapped[int] = mapped_column(ForeignKey("patrons.id"), nullable=False)
    checked_out_on: Mapped[date] = mapped_column(Date, nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    returned_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    renewals: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The last calendar date the overdue job notified this loan's patron, so
    # a loan is warned about at most once per day no matter how many times
    # the job runs.
    last_notified_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Any fine that had already accrued at the moment of the loan's most
    # recent renewal, locked in so extending the due date never erases it.
    frozen_fine: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00")
    )


class Hold(Base):
    __tablename__ = "holds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False)
    patron_id: Mapped[int] = mapped_column(ForeignKey("patrons.id"), nullable=False)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def ensure_aware_utc(dt: datetime) -> None:
    """Reject naive datetimes at the boundary.

    Every datetime this service stores or accepts is timezone-aware UTC;
    naive datetimes (the equivalent of `datetime.utcnow()`) are never
    allowed in. Plain `date` values, such as due dates, are not covered by
    this check.
    """
    if dt.tzinfo is None:
        raise ValueError("expected a timezone-aware datetime, got a naive one")


def _database_url() -> str:
    return environ.get("LIBRARY_DATABASE_URL", "sqlite://")


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(_database_url(), future=True)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def create_all() -> None:
    Base.metadata.create_all(get_engine())


@contextmanager
def session_scope(session_factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    """Yield a session, commit on success, roll back and re-raise on error.

    This is the only place a transaction is opened or closed. Repositories
    never call commit or rollback themselves; the API's `get_db` dependency
    and the overdue job are the only callers of this function (see
    sandbox/library/README.md).
    """
    factory = session_factory or get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
