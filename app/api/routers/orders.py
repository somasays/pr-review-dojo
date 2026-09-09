from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.api.deps import (
    AdminPrincipal,
    AppSettings,
    CurrentPrincipal,
    DbSession,
    Emails,
    Orders,
    PageParams,
)
from app.api.schemas import BasketIn, OrderCreate, OrderOut, Page, QuoteOut, ReorderCreate
from app.db.models import Order, OrderItem
from app.db.repositories import NotFound, OrderRepository
from app.domain.money import CENTS
from app.domain.order_state import InvalidTransition, OrderStatus
from app.domain.pricing import TAX_RATES, DiscountKind
from app.services.notification import Message
from app.services.order_service import CreateOrderCommand
from app.services.pricing_service import (
    DISCOUNT_CODES,
    InsufficientStock,
    ItemRequest,
    UnknownDiscountCode,
    UnknownSku,
)
from app.services.retry import RetryPolicy, retry

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(principal: CurrentPrincipal, body: OrderCreate, service: Orders) -> Order:
    cmd = CreateOrderCommand(
        customer_id=principal.customer,
        idempotency_key=body.idempotency_key,
        items=[ItemRequest(i.sku, i.quantity) for i in body.items],
        discount_codes=body.discount_codes,
    )
    try:
        return service.create(cmd)
    except (UnknownSku, UnknownDiscountCode) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except InsufficientStock as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("", response_model=Page[OrderOut])
def list_orders(db: DbSession, principal: CurrentPrincipal, page: PageParams) -> dict[str, object]:
    rows = OrderRepository(db).list_for_customer(
        principal.customer, limit=page.limit, offset=page.offset
    )
    return {"items": rows, "limit": page.limit, "offset": page.offset}


@router.post("/preview", response_model=QuoteOut)
def preview_basket(body: BasketIn, db: DbSession, principal: CurrentPrincipal) -> dict[str, object]:
    region = db.scalar(
        text("SELECT region FROM customers WHERE id = :cid"), {"cid": principal.customer}
    )
    products = {}
    for item in body.items:
        row = db.execute(
            text("SELECT unit_price, currency, stock FROM products WHERE sku = :sku"),
            {"sku": item.sku},
        ).first()
        if row is not None:
            products[item.sku] = row
    missing = [i.sku for i in body.items if i.sku not in products]
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown skus: {', '.join(missing)}"
        )

    sub = Decimal("0.00")
    for item in body.items:
        row = products[item.sku]
        if row.stock < item.quantity:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{item.sku}: requested {item.quantity}, available {row.stock}",
            )
        price = Decimal(str(row.unit_price)).quantize(CENTS, rounding=ROUND_HALF_UP)
        sub += (price * item.quantity).quantize(CENTS, rounding=ROUND_HALF_UP)

    disc = Decimal("0.00")
    code_used = None
    for code in body.discount_codes:
        normalized = code.strip().upper()
        if normalized not in DISCOUNT_CODES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, normalized)
        rule = DISCOUNT_CODES[normalized]
        if rule.kind is DiscountKind.PERCENT:
            off = (sub * rule.value / Decimal(100)).quantize(CENTS, rounding=ROUND_HALF_UP)
        elif rule.kind is DiscountKind.FIXED:
            off = rule.value.quantize(CENTS, rounding=ROUND_HALF_UP)
        elif rule.min_subtotal is not None and sub >= rule.min_subtotal.amount:
            off = (sub * rule.value / Decimal(100)).quantize(CENTS, rounding=ROUND_HALF_UP)
        else:
            off = Decimal("0.00")
        off = min(off, sub)
        if off > disc:
            disc, code_used = off, rule.code

    taxable = sub - disc
    tax = (taxable * TAX_RATES.get(region, Decimal("0")) / Decimal(100)).quantize(
        CENTS, rounding=ROUND_HALF_UP
    )
    return {
        "currency": products[body.items[0].sku].currency,
        "subtotal": sub,
        "discount": disc,
        "tax": tax,
        "total": taxable + tax,
        "discount_code": code_used,
    }


@router.post("/{order_id}/reorder", response_model=OrderOut, status_code=201)
def reorder(
    order_id: int,
    body: ReorderCreate,
    db: DbSession,
    principal: CurrentPrincipal,
    settings: AppSettings,
    sender: Emails,
) -> Order:
    if principal.is_admin:
        previous = db.execute(
            text("SELECT customer_id, discount_code FROM orders WHERE id = :oid"),
            {"oid": order_id},
        ).first()
        if previous is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")
    else:
        previous = db.execute(
            text(
                "SELECT customer_id, discount_code FROM orders "
                "WHERE id = :oid AND customer_id = :cid"
            ),
            {"oid": order_id, "cid": principal.customer},
        ).first()
        if previous is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")

    customer_id = previous.customer_id
    key = body.idempotency_key
    same_key = select(Order).where(Order.customer_id == customer_id, Order.idempotency_key == key)
    existing = db.scalar(same_key)
    if existing is not None:
        return existing

    customer = db.execute(
        text("SELECT email, region FROM customers WHERE id = :cid"), {"cid": customer_id}
    ).first()
    wanted = db.execute(
        text("SELECT sku, quantity FROM order_items WHERE order_id = :oid ORDER BY id"),
        {"oid": order_id},
    ).all()
    products = {}
    for item in wanted:
        row = db.execute(
            text("SELECT id, unit_price, currency, stock FROM products WHERE sku = :sku"),
            {"sku": item.sku},
        ).first()
        if row is not None:
            products[item.sku] = row
    missing = [i.sku for i in wanted if i.sku not in products]
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown skus: {', '.join(missing)}"
        )

    sub = Decimal("0.00")
    new_items = []
    for item in wanted:
        row = products[item.sku]
        if row.stock < item.quantity:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{item.sku}: requested {item.quantity}, available {row.stock}",
            )
        price = Decimal(str(row.unit_price)).quantize(CENTS, rounding=ROUND_HALF_UP)
        sub += (price * item.quantity).quantize(CENTS, rounding=ROUND_HALF_UP)
        new_items.append(
            OrderItem(product_id=row.id, sku=item.sku, quantity=item.quantity, unit_price=price)
        )

    disc = Decimal("0.00")
    code_used = None
    if previous.discount_code:
        normalized = previous.discount_code.strip().upper()
        if normalized not in DISCOUNT_CODES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, normalized)
        rule = DISCOUNT_CODES[normalized]
        if rule.kind is DiscountKind.PERCENT:
            off = (sub * rule.value / Decimal(100)).quantize(CENTS, rounding=ROUND_HALF_UP)
        elif rule.kind is DiscountKind.FIXED:
            off = rule.value.quantize(CENTS, rounding=ROUND_HALF_UP)
        elif rule.min_subtotal is not None and sub >= rule.min_subtotal.amount:
            off = (sub * rule.value / Decimal(100)).quantize(CENTS, rounding=ROUND_HALF_UP)
        else:
            off = Decimal("0.00")
        off = min(off, sub)
        if off > disc:
            disc, code_used = off, rule.code

    taxable = sub - disc
    tax = (taxable * TAX_RATES.get(customer.region, Decimal("0")) / Decimal(100)).quantize(
        CENTS, rounding=ROUND_HALF_UP
    )
    order = Order(
        customer_id=customer_id,
        idempotency_key=key,
        status=OrderStatus.PENDING_PAYMENT,
        currency=products[wanted[0].sku].currency,
        subtotal=sub,
        discount=disc,
        tax=tax,
        total=taxable + tax,
        discount_code=code_used,
    )
    for item in wanted:
        db.execute(
            text("UPDATE products SET stock = stock - :q WHERE sku = :sku"),
            {"q": item.quantity, "sku": item.sku},
        )
    try:
        with db.begin_nested():
            order.items = new_items
            db.add(order)
            db.flush()
    except IntegrityError:
        # Lost a race with a concurrent request using the same key.
        db.rollback()
        winner = db.scalar(same_key)
        assert winner is not None
        return winner

    message = Message(
        to=customer.email,
        subject=f"Order {order.id} placed from order {order_id}",
        body=f"We are preparing the same items again. Your total is {order.total}.",
        dedupe_key=f"order-reordered:{order.id}",
    )
    policy = RetryPolicy(
        attempts=settings.notify_retries, backoff_seconds=settings.notify_backoff_seconds
    )
    retry(lambda: sender.send(message), policy, sleep=lambda _s: None)
    return order


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, db: DbSession, principal: CurrentPrincipal) -> Order:
    repo = OrderRepository(db)
    try:
        if principal.is_admin:
            return repo.get(order_id)
        return repo.get_for_customer(order_id, principal.customer)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found") from exc


@router.post("/{order_id}/cancel", response_model=OrderOut)
def cancel_order(
    order_id: int, db: DbSession, principal: CurrentPrincipal, service: Orders
) -> Order:
    try:
        if not principal.is_admin:
            OrderRepository(db).get_for_customer(order_id, principal.customer)
        return service.cancel(order_id)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found") from exc
    except InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/{order_id}/pay", response_model=OrderOut)
def pay_order(order_id: int, _admin: AdminPrincipal, service: Orders) -> Order:
    try:
        return service.mark_paid(order_id)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found") from exc
    except InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/{order_id}/ship", response_model=OrderOut)
def ship_order(order_id: int, _admin: AdminPrincipal, service: Orders) -> Order:
    try:
        return service.ship(order_id)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found") from exc
    except InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
