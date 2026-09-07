from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, DbSession
from app.api.schemas import GiftCardOut
from app.db.models import GiftCard
from app.db.repositories import NotFound

router = APIRouter(prefix="/gift-cards", tags=["gift-cards"])


@router.get("/{code}/balance", response_model=GiftCardOut)
def get_balance(code: str, db: DbSession, _principal: CurrentPrincipal) -> GiftCard:
    row = db.scalar(select(GiftCard).where(GiftCard.code == code))
    if row is None:
        raise NotFound("gift card", code)
    return row
