"""Webhooks from the payment provider."""

from __future__ import annotations

import hmac
import logging
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status

from app.api.deps import AdminPrincipal, AppSettings, DbSession, Orders
from app.api.schemas import OrderOut, PaymentWebhookIn
from app.db.models import Order
from app.db.repositories import NotFound, OrderRepository
from app.domain.order_state import InvalidTransition, OrderStatus
from app.services.payments import AmountMismatch, PaymentEvent

log = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/webhook", response_model=OrderOut)
def payment_webhook(
    body: PaymentWebhookIn,
    settings: AppSettings,
    service: Orders,
    x_webhook_secret: Annotated[str | None, Header()] = None,
) -> Order:
    expected = settings.payment_webhook_secret
    if not expected or not hmac.compare_digest(x_webhook_secret or "", expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid webhook signature")
    event = PaymentEvent(
        order_id=body.order_id,
        provider_ref=body.provider_ref,
        amount=body.amount,
        currency=body.currency,
    )
    log.info("payment webhook %s for order %s", event.provider_ref, event.order_id)
    try:
        return service.apply_payment_event(event)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found") from exc
    except AmountMismatch as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/pending-review", response_model=list[OrderOut])
def pending_review(db: DbSession, _admin: AdminPrincipal) -> Sequence[Order]:
    """Orders still waiting on a payment webhook, for manual follow-up."""
    return OrderRepository(db).list_by_status(OrderStatus.PENDING_PAYMENT)
