"""FastAPI dependencies: database session, authentication, authorization."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.models import Customer
from app.db.repositories import CustomerRepository
from app.db.session import get_session_factory
from app.services.config import Settings, get_settings
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import OrderService
from app.services.pricing_service import PricingService


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    customer_id: int | None
    is_admin: bool

    @property
    def customer(self) -> int:
        if self.customer_id is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "admin key has no customer context")
        return self.customer_id


def get_principal(
    db: DbSession,
    settings: AppSettings,
    x_api_key: Annotated[str | None, Header()] = None,
) -> Principal:
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-API-Key")
    if x_api_key in settings.admin_api_keys:
        return Principal(customer_id=None, is_admin=True)
    customer: Customer | None = CustomerRepository(db).by_api_key_hash(hash_api_key(x_api_key))
    if customer is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")
    return Principal(customer_id=customer.id, is_admin=False)


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_admin(principal: CurrentPrincipal) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return principal


AdminPrincipal = Annotated[Principal, Depends(require_admin)]


@dataclass(frozen=True)
class Pagination:
    limit: int
    offset: int


def get_pagination(
    settings: AppSettings,
    limit: Annotated[int, Query(ge=1)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Pagination:
    return Pagination(limit=min(limit, settings.page_size_max), offset=offset)


PageParams = Annotated[Pagination, Depends(get_pagination)]

# Process-wide sender so local runs can inspect what would have been emailed.
_sender = InMemorySender()


def get_order_service(db: DbSession, settings: AppSettings) -> OrderService:
    return OrderService(db, PricingService(db), NotificationService(_sender, settings))


Orders = Annotated[OrderService, Depends(get_order_service)]
