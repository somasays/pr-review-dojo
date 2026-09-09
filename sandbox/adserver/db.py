"""Models, engine, session factory, and the transaction boundary for the
adserver service. See sandbox/adserver/README.md for conventions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from functools import lru_cache
from os import environ

from sqlalchemy import (
    Boolean,
    Date,
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

from sandbox.adserver.domain.pacing import CampaignStatus


class Base(DeclarativeBase):
    pass


class Advertiser(Base):
    __tablename__ = "advertisers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    advertiser_id: Mapped[int] = mapped_column(ForeignKey("advertisers.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    daily_budget: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    cpm: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default=CampaignStatus.ACTIVE.value
    )
    carryover_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    advertiser: Mapped[Advertiser] = relationship()


class Spend(Base):
    __tablename__ = "spend"
    __table_args__ = (UniqueConstraint("campaign_id", "day", name="uq_spend_campaign_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))


class Carryover(Base):
    __tablename__ = "carryovers"
    __table_args__ = (UniqueConstraint("campaign_id", "day", name="uq_carryover_campaign_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))


def _database_url() -> str:
    return environ.get("ADSERVER_DATABASE_URL", "sqlite://")


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
