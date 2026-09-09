"""SQLAlchemy 2.x models, engine, session factory, and the transaction
boundary for the timesheets service.

Conventions (see sandbox/timesheets/README.md): integer primary keys,
integer-minute durations, Decimal money quantized to cents and Decimal
rates with 4 decimal places, UTC-stored clock times, and the service owns
the transaction through unit_of_work(); repositories flush but never
commit.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
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
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)


class Base(DeclarativeBase):
    pass


class Worker(Base):
    __tablename__ = "workers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    hourly_rate: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Timesheet(Base):
    __tablename__ = "timesheets"
    __table_args__ = (
        UniqueConstraint(
            "worker_id", "period_start", "version", name="uq_timesheets_worker_period_version"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("workers.id"), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    total_pay: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)


class Shift(Base):
    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timesheet_id: Mapped[int] = mapped_column(ForeignKey("timesheets.id"), nullable=False)
    start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


def ensure_aware_utc(dt: datetime) -> None:
    """Reject a naive datetime; shift clock times are stored as aware UTC."""
    if dt.tzinfo is None:
        raise ValueError("expected a timezone-aware datetime, got a naive one")


def coerce_utc(dt: datetime) -> datetime:
    """Reattach UTC tzinfo dropped by SQLite's round-trip of a
    `DateTime(timezone=True)` column. A no-op if tzinfo is already set."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _database_url() -> str:
    return environ.get("TIMESHEETS_DATABASE_URL", "sqlite://")


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

    The only place a transaction opens or closes; repositories never
    commit or roll back themselves (see sandbox/timesheets/README.md).
    """
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
