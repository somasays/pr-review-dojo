"""Engine and session factory.

One engine per process, created lazily from settings. Request handlers and
services receive a Session and never create their own.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.services.config import get_settings


def _enable_sqlite_fks(dbapi_conn: object, _record: object) -> None:
    cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def build_engine(url: str | None = None) -> Engine:
    url = url or get_settings().database_url
    engine = create_engine(url, future=True)
    if url.startswith("sqlite"):
        event.listen(engine, "connect", _enable_sqlite_fks)
    return engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return build_engine()


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit on success, roll back on error, always close."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
