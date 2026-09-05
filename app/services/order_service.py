"""Order lifecycle: create, pay, ship, cancel, refund.

Every write is idempotent. Creation is keyed by (customer, idempotency_key);
status changes are guarded by the domain state machine and are no-ops when
the order is already in the target state.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Order, OrderItem
from app.db.repositories import CustomerRepository, OrderRepository, ProductRepository
from app.domain.money import Money
from app.domain.order_state import OrderStatus, is_cancellable, is_refundable, transition
from app.services.notification import NotificationService
from app.services.pricing_service import ItemRequest, PricingService

log = logging.getLogger(__name__)

LARGE_REFUND_THRESHOLD = Decimal("500")


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

    def refund_lines(self, order_id: int) -> list[tuple[str, Money]]:
        """What each line is worth once the order discount is spread across the lines."""
        order = self.orders.get(order_id)
        share = Money(order.discount / len(order.items), order.currency)
        lines = []
        for item in order.items:
            line_total = Money(item.unit_price, order.currency) * item.quantity
            lines.append((item.sku, line_total - share))
        return lines

    def refund(
        self, order_id: int, reason: str | None = None, notify_support: bool = True
    ) -> Order:
        """Refund a paid or delivered order, put the stock back, and email the customer."""
        order = self.orders.get(order_id)
        current = OrderStatus(order.status)
        if current is not OrderStatus.REFUNDED and not is_refundable(current):
            # Let the state machine raise the descriptive error.
            transition(current, OrderStatus.REFUNDED)
        for item in order.items:
            item.product.stock += item.quantity
        if current is OrderStatus.REFUNDED:
            log.info("order %s is already refunded, no second email", order_id)
            return order
        self._move(order, OrderStatus.REFUNDED)
        breakdown = ", ".join(f"{sku} {amount}" for sku, amount in self.refund_lines(order.id))
        self.notifications.order_refunded(
            order.customer.email,
            order.id,
            f"{float(order.total):.2f}",
            breakdown,
            reason=reason,
        )
        if notify_support:
            self.flag_large_refund(order.id, reason)
        return order

    def flag_large_refund(self, order_id: int, reason: str | None = None) -> str | None:
        """Support wants a CSV line for anything over the threshold, to paste into
        the refund spreadsheet."""
        order = self.orders.get(order_id)
        if order.total < LARGE_REFUND_THRESHOLD:
            return None
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([order.id, order.customer.email, str(order.total), reason or ""])
        for sku, amount in self.refund_lines(order_id):
            writer.writerow([order.id, sku, str(amount)])
        row = buf.getvalue()
        self.notifications.notify_support_of_large_refund(order.id, row)
        return row
