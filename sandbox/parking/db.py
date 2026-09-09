"""SQLAlchemy 2.x models, engine, session factory, and the transaction
boundary for the parking service. See sandbox/parking/README.md for the
conventions this module enforces."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from os import environ

from sqlalchemy import DateTime, Engine, ForeignKey, Integer, Numeric, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from sandbox.parking.domain.pricing import TicketStatus


class Base(DeclarativeBase):
    pass


class Garage(Base):
    __tablename__ = "garages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.id"), nullable=False)
    plate: Mapped[str] = mapped_column(String(20), nullable=False)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fee: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=TicketStatus.OPEN.value)


class Pass(Base):
    """A prepaid, half-open [valid_from, valid_to) window covering a
    plate's parking in one garage without a per-visit fee."""

    __tablename__ = "passes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.id"), nullable=False)
    plate: Mapped[str] = mapped_column(String(20), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)


def ensure_aware_utc(dt: datetime) -> None:
    """Reject a naive datetime; every datetime here is timezone-aware UTC."""
    if dt.tzinfo is None:
        raise ValueError("expected a timezone-aware datetime, got a naive one")


def coerce_utc(dt: datetime) -> datetime:
    """Reattach UTC tzinfo dropped by SQLite's round-trip of a
    `DateTime(timezone=True)` column. A no-op if tzinfo is already set."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _database_url() -> str:
    return environ.get("PARKING_DATABASE_URL", "sqlite://")


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
    wraps this as a FastAPI dependency (see sandbox/parking/README.md)."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
