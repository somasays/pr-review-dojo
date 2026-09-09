"""Order lifecycle: create, pay, ship, cancel, and fulfillment.

Every write is idempotent. Creation is keyed by (customer, idempotency_key);
status changes are guarded by the domain state machine and are no-ops when
the order is already in the target state.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Order, OrderItem
from app.db.repositories import CustomerRepository, OrderRepository, ProductRepository
from app.domain.money import Money
from app.domain.order_state import InvalidTransition, OrderStatus, is_cancellable, transition
from app.services.notification import NotificationService
from app.services.payment import InMemoryGateway, PaymentGateway
from app.services.pricing_service import ItemRequest, PricingService

log = logging.getLogger(__name__)


class FulfillmentFailed(Exception):
    """A fulfillment attempt was compensated. `cause` is the original failure."""

    def __init__(self, order_id: int, cause: BaseException) -> None:
        super().__init__(f"could not fulfill order {order_id}: {cause}")
        self.order_id = order_id
        self.cause = cause


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
        gateway: PaymentGateway | None = None,
    ) -> None:
        self.session = session
        self.orders = OrderRepository(session)
        self.customers = CustomerRepository(session)
        self.products = ProductRepository(session)
        self.pricing = pricing
        self.notifications = notifications
        self.gateway = gateway or InMemoryGateway()

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
        if q.total.is_zero():
            # Nothing to charge, so the customer hears from us straight away.
            self.notifications.order_confirmed(customer.email, order.id, str(q.total))
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
            self.notifications.order_confirmed(
                order.customer.email, order.id, f"{float(order.total):.2f}"
            )
        return order

    def ship(self, order_id: int, tracking_number: str | None = None) -> Order:
        order = self.orders.get(order_id)
        for item in order.items:
            # A product pulled from the catalog after the order was placed cannot ship.
            self.products.get(item.product_id)
        was_paid = order.status == OrderStatus.PAID
        self._move(order, OrderStatus.SHIPPED)
        if was_paid:
            self.notifications.order_shipped(order.customer.email, order.id, tracking_number)
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
        self.products.release(order.items)
        self._move(order, OrderStatus.CANCELLED)
        self.notifications.order_cancelled(order.customer.email, order.id)
        return order

    def refund(self, order_id: int) -> Order:
        return self._move(self.orders.get(order_id), OrderStatus.REFUNDED)

    def fulfill(self, order_id: int, tracking_number: str) -> Order:
        """Reserve stock, take the payment, and ship the order.

        Any failure is compensated: the charge is refunded, the stock goes back
        on the shelf, and the order is cancelled.
        """
        order = self.orders.get(order_id)
        if order.status == OrderStatus.SHIPPED:
            log.info("order %s is already shipped, nothing to fulfill", order.id)
            return order
        if OrderStatus(order.status) not in (OrderStatus.PENDING_PAYMENT, OrderStatus.PAID):
            raise InvalidTransition(OrderStatus(order.status), OrderStatus.SHIPPED)

        self.products.reserve(order.items)
        charge_id: str | None = None
        try:
            amount = Money(order.total, order.currency)
            if amount.is_zero():
                log.info("order %s has nothing to charge", order.id)
            else:
                charge_id = self.gateway.charge(amount, f"order:{order.id}")
            self.mark_paid(order.id)
            self.ship(order.id, tracking_number)
        except Exception as exc:
            log.warning("fulfillment of order %s failed: %s", order.id, exc)
            self._compensate(order, charge_id)
            raise FulfillmentFailed(order.id, exc) from exc
        return order

    def _compensate(self, order: Order, charge_id: str | None) -> None:
        if charge_id is not None:
            self.gateway.refund(charge_id, f"refund:{order.id}")
        self.products.release(order.items)
        try:
            self._move(order, OrderStatus.CANCELLED)
        except InvalidTransition as exc:
            log.info("order %s was not moved to cancelled: %s", order.id, exc)
        # Put the stock back on the storefront now, even when the email gateway is down.
        self.session.commit()
        self.notifications.order_cancelled(order.customer.email, order.id)

    def fulfill_batch(self, jobs: Sequence[tuple[int, str]]) -> list[Order]:
        """Fulfill several orders and tell the warehouse which ones went out."""
        shipped: list[Order] = []
        for order_id, tracking_number in jobs:
            try:
                shipped.append(self.fulfill(order_id, tracking_number))
            except FulfillmentFailed as exc:
                log.warning("skipping order %s: %s", order_id, exc)
        if shipped:
            self.notifications.warehouse_digest([o.id for o in shipped])
        log.info("fulfilled %d of %d orders", len(shipped), len(jobs))
        return shipped
