"""Repositories: the only place that builds queries.

Every method takes the Session it should use. Repositories never commit;
the caller owns the transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Customer, DiscountCode, Order, OrderItem, Product
from app.domain.order_state import OrderStatus


class NotFound(Exception):
    def __init__(self, entity: str, key: object) -> None:
        super().__init__(f"{entity} {key!r} not found")
        self.entity = entity
        self.key = key


class CustomerRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, customer_id: int) -> Customer:
        row = self.session.get(Customer, customer_id)
        if row is None:
            raise NotFound("customer", customer_id)
        return row

    def by_email(self, email: str) -> Customer | None:
        return self.session.scalar(select(Customer).where(Customer.email == email))

    def by_api_key_hash(self, key_hash: str) -> Customer | None:
        return self.session.scalar(select(Customer).where(Customer.api_key_hash == key_hash))

    def list(self, limit: int = 50, offset: int = 0) -> Sequence[Customer]:
        stmt = select(Customer).order_by(Customer.id).limit(limit).offset(offset)
        return self.session.scalars(stmt).all()

    def add(self, customer: Customer) -> Customer:
        self.session.add(customer)
        self.session.flush()
        return customer


class ProductRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, product_id: int) -> Product:
        row = self.session.get(Product, product_id)
        if row is None:
            raise NotFound("product", product_id)
        return row

    def by_skus(self, skus: Sequence[str]) -> dict[str, Product]:
        if not skus:
            return {}
        rows = self.session.scalars(select(Product).where(Product.sku.in_(skus))).all()
        return {p.sku: p for p in rows}

    def add(self, product: Product) -> Product:
        self.session.add(product)
        self.session.flush()
        return product


class OrderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, order_id: int) -> Order:
        row = self.session.get(Order, order_id)
        if row is None:
            raise NotFound("order", order_id)
        return row

    def get_for_customer(self, order_id: int, customer_id: int) -> Order:
        row = self.session.scalar(
            select(Order).where(Order.id == order_id, Order.customer_id == customer_id)
        )
        if row is None:
            raise NotFound("order", order_id)
        return row

    def by_idempotency_key(self, customer_id: int, key: str) -> Order | None:
        stmt = select(Order).where(Order.customer_id == customer_id, Order.idempotency_key == key)
        return self.session.scalar(stmt)

    def list_for_customer(
        self, customer_id: int, limit: int = 50, offset: int = 0
    ) -> Sequence[Order]:
        stmt = (
            select(Order)
            .where(Order.customer_id == customer_id)
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return self.session.scalars(stmt).all()

    def list_by_status(self, status: OrderStatus, limit: int = 100) -> Sequence[Order]:
        stmt = select(Order).where(Order.status == status).order_by(Order.id).limit(limit)
        return self.session.scalars(stmt).all()

    def created_between(self, start: datetime, end: datetime) -> Sequence[Order]:
        stmt = (
            select(Order)
            .where(Order.created_at >= start, Order.created_at < end)
            .order_by(Order.id)
        )
        return self.session.scalars(stmt).all()

    def add(self, order: Order, items: list[OrderItem]) -> Order:
        order.items = items
        self.session.add(order)
        self.session.flush()
        return order

    def count_by_status(self) -> dict[str, int]:
        stmt = select(Order.status, func.count(Order.id)).group_by(Order.status)
        return {status: count for status, count in self.session.execute(stmt).all()}


class DiscountCodeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def by_code(self, code: str) -> DiscountCode | None:
        return self.session.scalar(select(DiscountCode).where(DiscountCode.code == code))

    def list_all(self) -> Sequence[DiscountCode]:
        return self.session.scalars(select(DiscountCode).order_by(DiscountCode.code)).all()

    def add(self, discount: DiscountCode) -> DiscountCode:
        self.session.add(discount)
        self.session.flush()
        return discount

    def deactivate(self, code: str) -> DiscountCode:
        row = self.by_code(code)
        if row is None:
            raise NotFound("discount_code", code)
        row.active = False
        self.session.flush()
        self.session.commit()  # take it out of circulation right away
        return row

    def record_redemption(self, code: str) -> DiscountCode:
        row = self.by_code(code)
        if row is None:
            raise NotFound("discount_code", code)
        row.times_redeemed += 1
        self.session.flush()
        return row

    def import_many(self, rows: Sequence[DiscountCode]) -> list[str]:
        """Add a batch of new codes, skipping ones that already exist."""
        skipped: list[str] = []
        for row in rows:
            try:
                with self.session.begin_nested():
                    self.session.add(row)
                    self.session.flush()
            except IntegrityError:
                skipped.append(row.code)
        return skipped
