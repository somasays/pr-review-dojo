"""SQLAlchemy 2.x models, engine, session factory, and the transaction
boundary for the lockers service.

Conventions (see sandbox/lockers/README.md): integer primary keys, money in
integer cents, naive UTC datetimes (never timezone-aware -- ensure_naive_utc
is the boundary check), and the service owns the transaction through
unit_of_work(); repositories never commit.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from os import environ

from sqlalchemy import Boolean, DateTime, Engine, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Locker(Base):
    __tablename__ = "lockers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site: Mapped[str] = mapped_column(String(120), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Compartment(Base):
    __tablename__ = "compartments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    locker_id: Mapped[int] = mapped_column(ForeignKey("lockers.id"), nullable=False)
    size: Mapped[str] = mapped_column(String(1), nullable=False)
    occupied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Parcel(Base):
    __tablename__ = "parcels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    compartment_id: Mapped[int] = mapped_column(ForeignKey("compartments.id"), nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)
    pickup_code: Mapped[str] = mapped_column(String(6), nullable=False)
    deposited_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    picked_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    redirect_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


def ensure_naive_utc(dt: datetime) -> None:
    """Reject timezone-aware datetimes at the boundary.

    Every datetime this service stores or accepts is naive and UTC by
    convention (the equivalent of `datetime.utcnow()`); aware datetimes are
    never allowed in.
    """
    if dt.tzinfo is not None:
        raise ValueError(f"expected a naive UTC datetime, got tzinfo={dt.tzinfo!r}")


def _database_url() -> str:
    return environ.get("LOCKERS_DATABASE_URL", "sqlite://")


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

    This is the only place a transaction is opened or closed. Repositories
    never call commit or rollback themselves (see
    sandbox/lockers/README.md).
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
