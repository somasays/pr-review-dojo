from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import AdminPrincipal, AppSettings, DbSession
from app.api.schemas import DiscountCodeCreate, DiscountCodeOut
from app.db.models import DiscountCode
from app.db.repositories import DiscountCodeRepository, NotFound

router = APIRouter(prefix="/discounts", tags=["discounts"])


@router.get("", response_model=list[DiscountCodeOut])
def list_discounts(db: DbSession, _admin: AdminPrincipal) -> list[DiscountCode]:
    return list(DiscountCodeRepository(db).list_all())


def _to_row(item: DiscountCodeCreate) -> DiscountCode:
    return DiscountCode(
        code=item.code.strip().upper(),
        kind=item.kind,
        value=item.value,
        min_subtotal=item.min_subtotal,
        max_redemptions=item.max_redemptions,
    )


@router.post("", response_model=DiscountCodeOut, status_code=status.HTTP_201_CREATED)
def create_discount(
    db: DbSession, _admin: AdminPrincipal, body: DiscountCodeCreate
) -> DiscountCode:
    repo = DiscountCodeRepository(db)
    if repo.by_code(body.code.strip().upper()) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "discount code already exists")
    try:
        with db.begin_nested():
            return repo.add(_to_row(body))
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "discount code already exists") from exc


@router.post("/import", response_model=list[str])
def import_discounts(
    db: DbSession, _admin: AdminPrincipal, settings: AppSettings, body: list[DiscountCodeCreate]
) -> list[str]:
    """Bulk-load codes for a promotion; codes that already exist are skipped."""
    if len(body) > settings.page_size_max:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "import batch too large")
    skipped = DiscountCodeRepository(db).import_many([_to_row(item) for item in body])
    print("codes on file:", len(DiscountCodeRepository(db).list_all()))
    return skipped


@router.post("/{code}/deactivate", response_model=DiscountCodeOut)
def deactivate_discount(code: str, db: DbSession, _admin: AdminPrincipal) -> DiscountCode:
    try:
        return DiscountCodeRepository(db).deactivate(code.strip().upper())
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "discount code not found") from exc
