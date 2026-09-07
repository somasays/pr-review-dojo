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

from app.db.models import Order, OrderItem, Product
from app.db.repositories import CustomerRepository, OrderRepository, ProductRepository
from app.domain.dates import utcnow
from app.domain.flash_sale import FlashSale
from app.domain.money import Money
from app.domain.order_state import OrderStatus, is_cancellable, transition
from app.services.flash_sales import ACTIVE_SALES, SaleCapExceeded, SaleCounter
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
        sale_counter: SaleCounter | None = None,
    ) -> None:
        self.session = session
        self.orders = OrderRepository(session)
        self.customers = CustomerRepository(session)
        self.products = ProductRepository(session)
        self.pricing = pricing
        self.notifications = notifications
        self.sale_counter = sale_counter or SaleCounter()

    def create(self, cmd: CreateOrderCommand) -> Order:
        existing = self.orders.by_idempotency_key(cmd.customer_id, cmd.idempotency_key)
        if existing is not None:
            log.info("order %s already exists for key %s", existing.id, cmd.idempotency_key)
            return existing

        customer = self.customers.get(cmd.customer_id)
        products = self.products.by_skus([i.sku for i in cmd.items])
        sale_prices = self._flash_sale_prices(cmd, products)
        q = self.pricing.quote(
            cmd.items, products, cmd.discount_codes, customer.region, sale_prices
        )

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
                unit_price=(
                    sale_prices[i.sku].amount
                    if i.sku in sale_prices
                    else products[i.sku].unit_price
                ),
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

        for i in cmd.items:
            if i.sku in sale_prices:
                self.sale_counter.record_purchase(i.sku, cmd.customer_id, i.quantity)
        return order

    def _flash_sale_prices(
        self, cmd: CreateOrderCommand, products: dict[str, Product]
    ) -> dict[str, Money]:
        """Sale price for each item whose SKU has an active flash sale."""
        now = utcnow()
        overrides: dict[str, Money] = {}
        for item in cmd.items:
            sale = ACTIVE_SALES.get(item.sku)
            if sale is None or not sale.is_active(now):
                continue
            self._check_sale_cap(sale, cmd.customer_id, item.quantity)
            product = products[item.sku]
            overrides[item.sku] = sale.sale_price(Money(product.unit_price, product.currency))
        return overrides

    def _check_sale_cap(self, sale: FlashSale, customer_id: int, quantity: int) -> None:
        already = self.sale_counter.units_sold_by_customer(sale.sku, customer_id)
        if not sale.units_within_cap(already, quantity):
            raise SaleCapExceeded(sale.sku)

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
