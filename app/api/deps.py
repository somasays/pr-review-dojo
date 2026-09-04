"""FastAPI dependencies: database session, authentication, authorization."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.db.models import Customer
from app.db.repositories import CustomerRepository
from app.db.session import get_session_factory
from app.services.config import Settings, get_settings
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import OrderService
from app.services.pricing_service import PricingService
from app.services.rate_limiter import RateLimiter, RateLimitPolicy


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
    return OrderService(db, PricingService(), NotificationService(_sender, settings))


Orders = Annotated[OrderService, Depends(get_order_service)]

_rate_limiter: RateLimiter | None = None


@lru_cache(maxsize=1)
def rate_limit_policy() -> RateLimitPolicy:
    settings = get_settings()
    return RateLimitPolicy(
        limit=settings.rate_limit_per_minute,
        window_seconds=settings.rate_limit_window_seconds,
    )


def get_rate_limiter() -> RateLimiter:
    """Build the limiter on first use so importing the app spawns no threads."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(rate_limit_policy())
        _rate_limiter.start()
    return _rate_limiter


def rate_limit(
    _principal: CurrentPrincipal,
    response: Response,
    x_api_key: Annotated[str, Header()] = "",
) -> None:
    """Cap each API key at `RATE_LIMIT_PER_MINUTE` writes per window."""
    limiter = get_rate_limiter()
    decision = limiter.hit(hash_api_key(x_api_key))
    response.headers["X-RateLimit-Limit"] = str(limiter.policy.limit)
    response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
    if not decision.allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate limit exceeded",
            headers={"Retry-After": str(decision.retry_after)},
        )
