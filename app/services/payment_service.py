"""Card payments for an order.

The gateway is a small JSON API: POST /charges with the order reference and
the card token, 402 for a decline, 2xx with a charge id on success. The call
is keyed by the order reference so a retry after a timeout does not charge
the card twice.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.db.models import Order
from app.db.repositories import OrderRepository
from app.domain.order_state import OrderStatus, transition
from app.services.config import Settings, get_settings
from app.services.notification import NotificationService
from app.services.retry import RetryExhausted, RetryPolicy, retry

log = logging.getLogger(__name__)

DECLINED = 402


class PaymentDeclined(Exception):
    def __init__(self, order_id: int, reason: str) -> None:
        super().__init__(f"payment for order {order_id} declined: {reason}")
        self.order_id = order_id
        self.reason = reason


class PaymentGatewayError(Exception):
    pass


def charge_reference(order_id: int) -> str:
    return f"order-{order_id}"


def charge_payload(order: Order, card_token: str) -> dict[str, Any]:
    """The gateway request body for one order. Pure: no session, no client."""
    return {
        "reference": charge_reference(order.id),
        "amount_cents": int(order.total * 100),
        "currency": order.currency,
        "card_token": card_token,
        "customer_email": order.customer.email,
        "lines": [
            {
                "sku": item.sku,
                "quantity": item.quantity,
                "unit_price_cents": int(item.unit_price * 100),
            }
            for item in order.items
        ],
    }


class PaymentService:
    def __init__(
        self,
        session: Session,
        notifications: NotificationService,
        client: httpx.Client | None = None,
        settings: Settings | None = None,
    ) -> None:
        settings = settings or get_settings()
        self.session = session
        self.orders = OrderRepository(session)
        self.notifications = notifications
        self.client = client or httpx.Client(
            base_url=settings.payment_gateway_url, timeout=settings.payment_timeout_seconds
        )
        self.api_key = settings.payment_api_key
        self.policy = RetryPolicy(
            attempts=settings.payment_attempts, retry_on=(httpx.TransportError,)
        )

    def charge(self, order_id: int, card_token: str) -> Order:
        order = self.orders.get(order_id)
        if order.status == OrderStatus.PAID:
            # The gateway already has a charge under this reference.
            return order
        # Check before charging: a declined transition must not reach the card.
        target = transition(OrderStatus(order.status), OrderStatus.PAID)

        response = self._post(charge_payload(order, card_token))
        if response.status_code == DECLINED:
            raise PaymentDeclined(order.id, str(response.json().get("reason", "declined")))
        if response.status_code >= 400:
            raise PaymentGatewayError(f"gateway returned {response.status_code}")

        charge_id = str(response.json().get("id") or charge_reference(order.id))
        order.status = target
        self.session.flush()
        self.notifications.payment_received(
            order.customer.email, order.id, f"{order.currency} {order.total}", charge_id
        )
        return order

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Idempotency-Key": str(payload["reference"]),
        }
        try:
            return retry(
                lambda: self.client.post("/charges", json=payload, headers=headers), self.policy
            )
        except RetryExhausted as exc:
            raise PaymentGatewayError(
                f"gateway unreachable after {exc.attempts} attempts: {exc.last!r}"
            ) from exc
