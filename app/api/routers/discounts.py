from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import AdminPrincipal, DbSession
from app.api.schemas import DiscountCodeCreate, DiscountCodeOut, DiscountUsageOut
from app.db.models import DiscountCode
from app.db.repositories import DiscountCodeRepository, NotFound

router = APIRouter(prefix="/discounts", tags=["discounts"])


@router.get("", response_model=list[DiscountCodeOut])
def list_discounts(db: DbSession, _admin: AdminPrincipal) -> list[DiscountCode]:
    return list(DiscountCodeRepository(db).list_all())


@router.post("", response_model=DiscountCodeOut, status_code=status.HTTP_201_CREATED)
def create_discount(
    db: DbSession, _admin: AdminPrincipal, body: DiscountCodeCreate
) -> DiscountCode:
    repo = DiscountCodeRepository(db)
    code = body.code.strip().upper()
    if repo.by_code(code) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "discount code already exists")
    return repo.add(
        DiscountCode(
            code=code,
            kind=body.kind,
            value=float(body.value),
            min_subtotal=body.min_subtotal,
            max_redemptions=body.max_redemptions,
        )
    )


@router.get("/usage", response_model=list[DiscountUsageOut])
def discount_usage(db: DbSession, _admin: AdminPrincipal) -> list[DiscountUsageOut]:
    repo = DiscountCodeRepository(db)
    out: list[DiscountUsageOut] = []
    for row in repo.list_all():
        orders = list(repo.redeemed_orders(row.code))
        units = 0
        buyers: set[str] = set()
        for order in orders:
            buyers.add(order.customer.email)
            for item in order.items:
                units += item.quantity
        out.append(
            DiscountUsageOut(
                code=row.code,
                orders=len(orders),
                units=units,
                customers=len(buyers),
                redemptions=repo.redemption_count(row.code),
            )
        )
    return out


@router.post("/{code}/deactivate", response_model=DiscountCodeOut)
def deactivate_discount(code: str, db: DbSession, _admin: AdminPrincipal) -> DiscountCode:
    try:
        return DiscountCodeRepository(db).deactivate(code.strip().upper())
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "discount code not found") from exc
