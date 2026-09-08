"""Hidden tests for exercise 37: parcel redirection."""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from sandbox.lockers.db import Base, Compartment, Locker, Parcel
from sandbox.lockers.domain.fit import Dimensions, expires_at
from sandbox.lockers.service import (
    HOLD_HOURS,
    DepositService,
    InvalidCode,
    NoSpace,
    RedirectService,
)

SMALL = Dimensions(10, 10, 10)
NOW = datetime(2026, 9, 8, 9, 0)


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Session:
    return session_factory()


def _make_locker(
    db: Session, site: str = "Main St", sizes: tuple[str, ...] = ("S", "M", "L")
) -> Locker:
    loc = Locker(site=site, active=True)
    db.add(loc)
    db.flush()
    for size in sizes:
        db.add(Compartment(locker_id=loc.id, size=size, occupied=False))
    db.commit()
    return loc


def test_redirect_leaves_the_old_compartment_alone_when_the_target_is_full(
    session_factory: sessionmaker[Session], db: Session
) -> None:
    """SA-02: a failed target allocation must not leave the source compartment freed."""
    source = _make_locker(db, sizes=("S",))
    target = _make_locker(db, sizes=())

    parcel = DepositService(session_factory).deposit(source.id, SMALL, "ada@example.com", NOW)
    with pytest.raises(NoSpace):
        RedirectService(session_factory).redirect(
            source.id, parcel.id, parcel.pickup_code, target.id, NOW
        )

    check = session_factory()
    compartment = check.get(Compartment, parcel.compartment_id)
    assert compartment is not None
    assert compartment.occupied is True


def test_redirect_keeps_the_original_deposit_time(
    session_factory: sessionmaker[Session], db: Session
) -> None:
    """SV-11: late fee math depends on the original deposit time surviving a redirect."""
    source = _make_locker(db)
    target = _make_locker(db)

    parcel = DepositService(session_factory).deposit(source.id, SMALL, "ada@example.com", NOW)
    original_deposited_at = parcel.deposited_at
    original_expires_at = parcel.expires_at
    assert original_expires_at == expires_at(NOW, HOLD_HOURS)

    later = NOW + timedelta(hours=5)
    redirected = RedirectService(session_factory).redirect(
        source.id, parcel.id, parcel.pickup_code, target.id, later
    )

    assert redirected.deposited_at == original_deposited_at
    assert redirected.expires_at == original_expires_at


def test_redirect_checks_code_uniqueness_in_the_target_locker(
    session_factory: sessionmaker[Session], db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FA-09: the new code must be unique among the target locker's active parcels."""
    source = _make_locker(db)
    target = _make_locker(db)

    taken_compartment = db.scalars(
        select(Compartment).where(Compartment.locker_id == target.id, Compartment.size == "M")
    ).one()
    taken_compartment.occupied = True
    db.add(
        Parcel(
            compartment_id=taken_compartment.id,
            recipient_email="bea@example.com",
            pickup_code="111111",
            deposited_at=NOW,
            expires_at=expires_at(NOW, HOLD_HOURS),
        )
    )
    db.commit()

    parcel = DepositService(session_factory).deposit(source.id, SMALL, "ada@example.com", NOW)
    values = itertools.chain([111111, 222222], itertools.repeat(222222))
    monkeypatch.setattr("sandbox.lockers.service.secrets.randbelow", lambda _n: next(values))

    redirected = RedirectService(session_factory).redirect(
        source.id, parcel.id, parcel.pickup_code, target.id, NOW
    )
    assert redirected.pickup_code == "222222"


def test_redirect_requires_the_correct_pickup_code(
    session_factory: sessionmaker[Session], db: Session
) -> None:
    """FA-03: redirecting requires proof of the current pickup code."""
    source = _make_locker(db)
    target = _make_locker(db)

    parcel = DepositService(session_factory).deposit(source.id, SMALL, "ada@example.com", NOW)
    wrong_code = "000000" if parcel.pickup_code != "000000" else "111111"

    with pytest.raises(InvalidCode):
        RedirectService(session_factory).redirect(source.id, parcel.id, wrong_code, target.id, NOW)


def test_redirect_service_notifies_through_an_injected_notifier(
    session_factory: sessionmaker[Session], db: Session
) -> None:
    """DS-04: redirect notification must go through an injectable seam, like sweep()."""
    source = _make_locker(db)
    target = _make_locker(db)

    notified: list[Parcel] = []
    redirect_service = RedirectService(session_factory, notifier=notified.append)
    parcel = DepositService(session_factory).deposit(source.id, SMALL, "ada@example.com", NOW)
    redirect_service.redirect(source.id, parcel.id, parcel.pickup_code, target.id, NOW)

    assert len(notified) == 1
    assert notified[0].id == parcel.id


def test_can_redirect_has_a_direct_test() -> None:
    """DS-22: every public function needs a test of its own (README convention 6)."""
    tests_dir = Path(__file__).resolve().parents[1] / "sandbox" / "lockers" / "tests"
    found = any("can_redirect" in p.read_text() for p in tests_dir.glob("test_*.py"))
    assert found
