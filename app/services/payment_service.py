"""Card payments for an order.

The gateway is a small JSON API: POST /charges with the order reference and
the card token, 402 for a decline, 2xx with a charge id on success. The call
is keyed by the order reference so a retry after a timeout does not charge
the card twice.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Order
from app.db.repositories import NotFound
from app.domain.order_state import InvalidTransition, OrderStatus
from app.services.notification import Message, NotificationService

log = logging.getLogger(__name__)


class PaymentDeclined(Exception):
    def __init__(self, order_id: int, reason: str) -> None:
        super().__init__(f"payment for order {order_id} declined: {reason}")
        self.order_id = order_id
        self.reason = reason


class PaymentGatewayError(Exception):
    pass


class PaymentService:
    def __init__(
        self,
        session: Session,
        notifications: NotificationService,
        client: httpx.Client | None = None,
    ) -> None:
        self.session = session
        self.notifications = notifications
        self.base_url = os.environ.get("PAYMENT_GATEWAY_URL", "https://payments.invalid")
        self.api_key = os.environ.get("PAYMENT_API_KEY", "")
        self.attempts = int(os.environ.get("PAYMENT_MAX_ATTEMPTS", "3"))
        self.client = client or httpx.Client(base_url=self.base_url, timeout=10.0)

    def charge(self, order_id: int, card_token: str) -> Order:
        try:
            order = self.session.scalar(select(Order).where(Order.id == order_id))
            if order is None:
                raise NotFound("order", order_id)
            if order.status == "paid":
                # The gateway already has a charge under this reference.
                return order
            if order.status != "pending_payment":
                raise InvalidTransition(OrderStatus(order.status), OrderStatus.PAID)

            reference = f"order-{order.id}"
            lines: list[dict[str, Any]] = []
            for item in order.items:
                lines.append(
                    {
                        "sku": item.sku,
                        "quantity": item.quantity,
                        "unit_price_cents": int(item.unit_price * 100),
                    }
                )
            payload: dict[str, Any] = {
                "reference": reference,
                "amount_cents": int(order.total * 100),
                "currency": order.currency,
                "card_token": card_token,
                "customer_email": order.customer.email,
                "lines": lines,
            }

            response = None
            last_error: Exception | None = None
            for attempt in range(1, self.attempts + 1):
                if attempt > 1:
                    time.sleep(0.2 * 2 ** (attempt - 2))
                try:
                    response = self.client.post(
                        "/charges",
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Idempotency-Key": reference,
                        },
                    )
                    break
                except httpx.TransportError as exc:
                    last_error = exc
                    log.warning("charge attempt %d/%d failed: %s", attempt, self.attempts, exc)
            if response is None:
                raise PaymentGatewayError(
                    f"gateway unreachable after {self.attempts} attempts: {last_error!r}"
                )
            if response.status_code == 402:
                raise PaymentDeclined(order.id, str(response.json().get("reason", "declined")))
            if response.status_code >= 400:
                raise PaymentGatewayError(f"gateway returned {response.status_code}")

            charge_id = str(response.json().get("id") or reference)
            order.status = "paid"
            self.session.flush()

            self.notifications.sender.send(
                Message(
                    to=order.customer.email,
                    subject=f"Payment received for order {order.id}",
                    body=(
                        f"We charged {order.currency} {order.total} to your card. "
                        f"Gateway reference {charge_id}."
                    ),
                    dedupe_key=f"payment-received:{order.id}",
                )
            )
            return order
        except Exception:
            log.exception("charge failed for order %s", order_id)
            raise
