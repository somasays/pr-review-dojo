from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import AdminPrincipal, CurrentPrincipal, DbSession, Orders, PageParams
from app.api.schemas import BasketIn, OrderCreate, OrderOut, Page, QuoteOut, ReorderCreate
from app.db.models import Order
from app.db.repositories import NotFound, OrderRepository
from app.domain.order_state import InvalidTransition
from app.services.order_service import CreateOrderCommand
from app.services.pricing_service import (
    InsufficientStock,
    ItemRequest,
    UnknownDiscountCode,
    UnknownSku,
)

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
def preview_basket(
    body: BasketIn, principal: CurrentPrincipal, service: Orders
) -> dict[str, object]:
    items = [ItemRequest(i.sku, i.quantity) for i in body.items]
    try:
        quote = service.preview(principal.customer, items, body.discount_codes)
    except (UnknownSku, UnknownDiscountCode) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except InsufficientStock as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {
        "currency": quote.total.currency,
        "subtotal": quote.subtotal.amount,
        "discount": quote.discount.amount,
        "tax": quote.tax.amount,
        "total": quote.total.amount,
        "discount_code": quote.applied_codes[0] if quote.applied_codes else None,
    }


@router.post("/{order_id}/reorder", response_model=OrderOut, status_code=201)
def reorder(
    order_id: int, body: ReorderCreate, principal: CurrentPrincipal, service: Orders
) -> Order:
    customer_id = None if principal.is_admin else principal.customer
    try:
        return service.reorder(order_id, body.idempotency_key, customer_id)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found") from exc
    except (UnknownSku, UnknownDiscountCode) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except InsufficientStock as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


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
