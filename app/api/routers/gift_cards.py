from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentPrincipal, DbSession
from app.api.schemas import GiftCardOut
from app.db.models import GiftCard
from app.db.repositories import GiftCardRepository

router = APIRouter(prefix="/gift-cards", tags=["gift-cards"])


@router.get("/{code}/balance", response_model=GiftCardOut)
def get_balance(code: str, db: DbSession, _principal: CurrentPrincipal) -> GiftCard:
    return GiftCardRepository(db).get_by_code(code)
