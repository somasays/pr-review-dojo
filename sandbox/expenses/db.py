"""SQLAlchemy 2.x models, engine, session factory, and the transaction
boundary for the expenses service.

Conventions (see sandbox/expenses/README.md): UUID4 string ids, Decimal
money quantized to cents with a currency code on every Claim, aware UTC
datetimes, and the service owns the transaction through unit_of_work();
repositories flush but never commit.
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
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)


class Base(DeclarativeBase):
    pass


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    manager_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PayoutBatch(Base):
    __tablename__ = "payout_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (
        UniqueConstraint("employee_id", "idempotency_key", name="uq_claims_employee_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    paid_in_batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("payout_batches.id"), nullable=True
    )

    lines: Mapped[list[ClaimLine]] = relationship(
        back_populates="claim", cascade="all, delete-orphan"
    )


class ClaimLine(Base):
    __tablename__ = "claim_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    incurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    claim: Mapped[Claim] = relationship(back_populates="lines")


def ensure_aware_utc(dt: datetime) -> None:
    """Reject a naive datetime; every datetime here is timezone-aware UTC."""
    if dt.tzinfo is None:
        raise ValueError("expected a timezone-aware datetime, got a naive one")


def _database_url() -> str:
    return environ.get("EXPENSES_DATABASE_URL", "sqlite://")


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
    commit or roll back themselves (see sandbox/expenses/README.md).
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
