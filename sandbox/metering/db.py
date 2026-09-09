"""SQLAlchemy 2.x models, engine, session factory, and the transaction
boundary for the metering service.

Conventions (see sandbox/metering/README.md): integer primary keys,
readings are an append-only ledger (never updated or deleted; a correction
is a new row with `supersedes_id`), Decimal kWh at 3 places and Decimal
money quantized to cents, aware UTC timestamps, and the API dependency
owns the transaction through `session_scope()`; services and repositories
flush but never commit.
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
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Meter(Base):
    __tablename__ = "meters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    serial: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    max_reading: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)


class Reading(Base):
    """One row of the append-only ledger; never updated or deleted. A
    correction is a new row whose `supersedes_id` points at the row it
    replaces (see sandbox/metering/README.md)."""

    __tablename__ = "readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meter_id: Mapped[int] = mapped_column(ForeignKey("meters.id"), nullable=False)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value_kwh: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("readings.id"), nullable=True)


class Bill(Base):
    """A bill, or a bill adjustment: a Bill row with `adjusts_bill_id`
    pointing at the bill it adjusts and `correction_reading_id` recording
    the correction that produced it. The bill it adjusts is never
    modified."""

    __tablename__ = "bills"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "period_start",
            "period_end",
            "adjusts_bill_id",
            name="uq_bills_account_period",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    kwh: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    adjusts_bill_id: Mapped[int | None] = mapped_column(ForeignKey("bills.id"), nullable=True)
    correction_reading_id: Mapped[int | None] = mapped_column(
        ForeignKey("readings.id"), nullable=True
    )


def ensure_aware_utc(dt: datetime) -> None:
    """Reject a naive datetime; every datetime here is timezone-aware UTC."""
    if dt.tzinfo is None:
        raise ValueError("expected a timezone-aware datetime, got a naive one")


def coerce_utc(dt: datetime) -> datetime:
    """Reattach UTC tzinfo dropped by SQLite's round-trip of a
    `DateTime(timezone=True)` column. A no-op if tzinfo is already set."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _database_url() -> str:
    return environ.get("METERING_DATABASE_URL", "sqlite://")


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(_database_url(), future=True)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def create_all() -> None:
    Base.metadata.create_all(get_engine())


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Yield a session, commit on success, roll back and re-raise on
    error. The only place a transaction opens or closes; `api.get_db`
    wraps this as a FastAPI dependency (see sandbox/metering/README.md)."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
