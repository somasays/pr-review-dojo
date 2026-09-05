from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import AdminPrincipal, CurrentPrincipal, DbSession, Orders, PageParams, Principal
from app.api.schemas import OrderCreate, OrderNoteIn, OrderOut, Page
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


def _scope_order(db: Session, order_id: int, principal: Principal) -> Order:
    repo = OrderRepository(db)
    if principal.is_admin:
        return repo.get(order_id)
    return repo.get_for_customer(order_id, principal.customer)


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, db: DbSession, principal: CurrentPrincipal) -> Order:
    try:
        return _scope_order(db, order_id, principal)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found") from exc


@router.patch("/{order_id}/notes", response_model=OrderOut)
def add_order_note(
    order_id: int, note: OrderNoteIn, db: DbSession, principal: CurrentPrincipal, service: Orders
) -> Order:
    """Attach a free-text note to an order.

    Returns the order with the new note attached.
    """
    try:
        _scope_order(db, order_id, principal)
        author = "admin" if principal.is_admin else f"customer:{principal.customer}"
        return service.add_note(order_id, note.body, author)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found") from exc
    except InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


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
