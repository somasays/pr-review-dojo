from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from app.api.deps import AdminPrincipal, DbSession, SaleCounterDep
from app.api.schemas import ActiveSaleOut, StatusCount
from app.db.repositories import OrderRepository
from app.domain.flash_sale import FlashSale
from app.services.flash_sales import ACTIVE_SALES

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/orders/by-status", response_model=list[StatusCount])
def orders_by_status(db: DbSession, _admin: AdminPrincipal) -> list[StatusCount]:
    counts = OrderRepository(db).count_by_status()
    return [StatusCount(status=k, count=v) for k, v in sorted(counts.items())]


@router.get("/orders/recent-total")
def recent_total(db: DbSession, _admin: AdminPrincipal, days: int = 7) -> dict[str, object]:
    end = datetime.now(tz=UTC)
    start = end - timedelta(days=days)
    rows = OrderRepository(db).created_between(start, end)
    total = sum((o.total for o in rows), start=0)
    return {"days": days, "orders": len(rows), "total": str(total)}


def format_active_sale(sku: str, sale: FlashSale, units_sold: int) -> ActiveSaleOut:
    return ActiveSaleOut(
        sku=sku,
        percent_off=sale.percent_off,
        floor_price=sale.floor_price.amount,
        per_customer_unit_cap=sale.per_customer_unit_cap,
        ends_at=sale.ends_at,
        units_sold=units_sold,
    )


@router.get("/sales/active", response_model=list[ActiveSaleOut])
def active_sales(_admin: AdminPrincipal, sale_counter: SaleCounterDep) -> list[ActiveSaleOut]:
    now = datetime.now(tz=UTC)
    return [
        format_active_sale(sku, sale, sale_counter.units_sold(sku))
        for sku, sale in sorted(ACTIVE_SALES.items())
        if sale.is_active(now)
    ]
