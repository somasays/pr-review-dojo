"""Repositories: the only place that builds queries.

Every method takes the Session it should use. Repositories never commit;
the caller owns the transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, and_, bindparam, func, or_, select, text
from sqlalchemy.orm import Session, selectinload

from app.db.models import Customer, Order, OrderItem, Product
from app.domain.money import CENTS
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

    def export_page(
        self,
        customer_id: int,
        statuses: Sequence[str],
        start: datetime,
        end: datetime,
        cursor: tuple[datetime, int] | None = None,
        limit: int = 100,
    ) -> Sequence[Order]:
        """One page of a customer's order history, newest first."""
        stmt = (
            select(Order)
            .options(selectinload(Order.items).selectinload(OrderItem.product))
            .where(
                Order.customer_id == customer_id,
                Order.created_at >= start,
                Order.created_at < end,
            )
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(limit)
        )
        if statuses:
            stmt = stmt.where(Order.status.in_(statuses))
        if cursor is not None:
            seen_at, seen_id = cursor
            stmt = stmt.where(
                or_(
                    Order.created_at < seen_at,
                    and_(Order.created_at == seen_at, Order.id < seen_id),
                )
            )
        return self.session.scalars(stmt).all()

    def export_summary(
        self, customer_id: int, statuses: Sequence[str], start: datetime, end: datetime
    ) -> tuple[int, Decimal]:
        """Order count and gross total for one export filter."""
        wanted = list(statuses) or [s.value for s in OrderStatus]
        stmt = text(
            "SELECT count(*) AS orders, coalesce(sum(total), 0) AS gross FROM orders "
            "WHERE customer_id = :cid AND status IN :statuses "
            "AND created_at >= :start AND created_at < :end"
        ).bindparams(
            bindparam("statuses", expanding=True),
            bindparam("start", type_=DateTime(timezone=True)),
            bindparam("end", type_=DateTime(timezone=True)),
        )
        row = self.session.execute(
            stmt, {"cid": customer_id, "statuses": wanted, "start": start, "end": end}
        ).one()
        return int(row.orders), Decimal(str(row.gross)).quantize(CENTS)

    def count_for_export(self, customer_id: int, statuses: Sequence[str]) -> int:
        """How many orders the export filter matches, ignoring the date window."""
        stmt = select(func.count(Order.id)).where(Order.customer_id == customer_id)
        if statuses:
            stmt = stmt.where(Order.status.in_(statuses))
        return self.session.scalar(stmt) or 0

    def add(self, order: Order, items: list[OrderItem]) -> Order:
        order.items = items
        self.session.add(order)
        self.session.flush()
        return order

    def count_by_status(self) -> dict[str, int]:
        stmt = select(Order.status, func.count(Order.id)).group_by(Order.status)
        return {status: count for status, count in self.session.execute(stmt).all()}
