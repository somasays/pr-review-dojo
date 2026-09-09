"""Order lifecycle: create, pay, ship, cancel.

Every write is idempotent. Creation is keyed by (customer, idempotency_key);
status changes are guarded by the domain state machine and are no-ops when
the order is already in the target state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Order, OrderItem
from app.db.repositories import (
    CustomerRepository,
    DiscountCodeRepository,
    OrderRepository,
    ProductRepository,
)
from app.domain.order_state import OrderStatus, is_cancellable, transition
from app.services.notification import NotificationService
from app.services.pricing_service import ItemRequest, PricingService

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreateOrderCommand:
    customer_id: int
    idempotency_key: str
    items: list[ItemRequest]
    discount_codes: list[str]


class OrderService:
    def __init__(
        self,
        session: Session,
        pricing: PricingService,
        notifications: NotificationService,
    ) -> None:
        self.session = session
        self.orders = OrderRepository(session)
        self.customers = CustomerRepository(session)
        self.products = ProductRepository(session)
        self.discounts = DiscountCodeRepository(session)
        self.pricing = pricing
        self.notifications = notifications

    def create(self, cmd: CreateOrderCommand) -> Order:
        existing = self.orders.by_idempotency_key(cmd.customer_id, cmd.idempotency_key)
        if existing is not None:
            log.info("order %s already exists for key %s", existing.id, cmd.idempotency_key)
            return existing

        customer = self.customers.get(cmd.customer_id)
        products = self.products.by_skus([i.sku for i in cmd.items])
        q = self.pricing.quote(cmd.items, products, cmd.discount_codes, customer.region)

        if q.applied_codes:
            self.discounts.record_redemption(q.applied_codes[0])
            # Persist the redemption before building the order so a concurrent
            # create cannot spend the same remaining redemption.
            self.session.commit()

        order = Order(
            customer_id=customer.id,
            idempotency_key=cmd.idempotency_key,
            status=OrderStatus.PENDING_PAYMENT,
            currency=q.total.currency,
            subtotal=q.subtotal.amount,
            discount=q.discount.amount,
            tax=q.tax.amount,
            total=q.total.amount,
            discount_code=q.applied_codes[0] if q.applied_codes else None,
        )
        items = [
            OrderItem(
                product_id=products[i.sku].id,
                sku=i.sku,
                quantity=i.quantity,
                unit_price=products[i.sku].unit_price,
            )
            for i in cmd.items
        ]
        for i in cmd.items:
            products[i.sku].stock -= i.quantity

        try:
            with self.session.begin_nested():
                self.orders.add(order, items)
        except IntegrityError:
            # Lost a race with a concurrent request using the same key.
            log.info("concurrent create for key %s, returning winner", cmd.idempotency_key)
            self.session.rollback()
            winner = self.orders.by_idempotency_key(cmd.customer_id, cmd.idempotency_key)
            assert winner is not None
            return winner
        return order

    def _move(self, order: Order, target: OrderStatus) -> Order:
        current = OrderStatus(order.status)
        if current is target:
            return order
        order.status = transition(current, target)
        self.session.flush()
        return order

    def mark_paid(self, order_id: int) -> Order:
        order = self.orders.get(order_id)
        was_pending = order.status == OrderStatus.PENDING_PAYMENT
        self._move(order, OrderStatus.PAID)
        if was_pending:
            self.notifications.order_confirmed(order.customer.email, order.id, str(order.total))
        return order

    def ship(self, order_id: int) -> Order:
        order = self.orders.get(order_id)
        was_paid = order.status == OrderStatus.PAID
        self._move(order, OrderStatus.SHIPPED)
        if was_paid:
            self.notifications.order_shipped(order.customer.email, order.id)
        return order

    def deliver(self, order_id: int) -> Order:
        return self._move(self.orders.get(order_id), OrderStatus.DELIVERED)

    def cancel(self, order_id: int) -> Order:
        order = self.orders.get(order_id)
        if order.status == OrderStatus.CANCELLED:
            return order
        if not is_cancellable(OrderStatus(order.status)):
            # Let the state machine raise the descriptive error.
            transition(OrderStatus(order.status), OrderStatus.CANCELLED)
        for item in order.items:
            item.product.stock += item.quantity
        self._move(order, OrderStatus.CANCELLED)
        self.notifications.order_cancelled(order.customer.email, order.id)
        return order

    def refund(self, order_id: int) -> Order:
        return self._move(self.orders.get(order_id), OrderStatus.REFUNDED)
