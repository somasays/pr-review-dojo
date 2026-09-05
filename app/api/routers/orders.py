from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import (
    AdminPrincipal,
    CurrentPrincipal,
    DbSession,
    Orders,
    PageParams,
    Reservations,
)
from app.api.schemas import HoldCreate, HoldOut, OrderCreate, OrderOut, Page
from app.db.models import Order
from app.db.repositories import NotFound, OrderRepository, ProductRepository
from app.domain.order_state import InvalidTransition
from app.services.order_service import CreateOrderCommand
from app.services.pricing_service import (
    InsufficientStock,
    ItemRequest,
    UnknownDiscountCode,
    UnknownSku,
)
from app.services.reservations import Hold

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


@router.post("/holds", response_model=HoldOut, status_code=status.HTTP_201_CREATED)
def create_hold(
    _principal: CurrentPrincipal, body: HoldCreate, db: DbSession, cache: Reservations
) -> Hold:
    """Hold stock for the customer while they finish paying."""
    product = ProductRepository(db).by_skus([body.sku]).get(body.sku)
    if product is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown sku: {body.sku}")
    hold = cache.reserve(body.sku, body.quantity, product.stock)
    if hold is None:
        left = cache.available(body.sku, product.stock)
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{body.sku}: requested {body.quantity}, available {left}"
        )
    return hold


@router.delete("/holds/{token}", status_code=status.HTTP_204_NO_CONTENT)
def release_hold(token: str, _principal: CurrentPrincipal, cache: Reservations) -> None:
    if not cache.release(token):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "hold not found")


@router.get("", response_model=Page[OrderOut])
def list_orders(db: DbSession, principal: CurrentPrincipal, page: PageParams) -> dict[str, object]:
    rows = OrderRepository(db).list_for_customer(
        principal.customer, limit=page.limit, offset=page.offset
    )
    return {"items": rows, "limit": page.limit, "offset": page.offset}


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
