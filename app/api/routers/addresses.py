from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import AppSettings, CurrentPrincipal, DbSession, PageParams
from app.api.schemas import AddressCreate, AddressOut, Page
from app.db.models import Address
from app.db.repositories import AddressRepository, NotFound

router = APIRouter(prefix="/customers/me/addresses", tags=["addresses"])


def _load(db: Session, address_id: int, customer_id: int) -> Address:
    try:
        return AddressRepository(db).get_for_customer(address_id, customer_id)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "address not found") from exc


@router.post("", response_model=AddressOut, status_code=status.HTTP_201_CREATED)
def create_address(db: DbSession, principal: CurrentPrincipal, body: AddressCreate) -> Address:
    """Add an address to the caller's address book. Returns 201 with the created record."""
    repo = AddressRepository(db)
    address = Address(
        customer_id=principal.customer,
        label=body.label,
        line1=body.line1,
        city=body.city,
        postal_code=body.postal_code,
        region=body.region,
        is_default=repo.default_for(principal.customer) is None,
    )
    return repo.add(address)


@router.get("", response_model=Page[AddressOut])
def list_addresses(
    db: DbSession, principal: CurrentPrincipal, page: PageParams
) -> dict[str, object]:
    rows = AddressRepository(db).list_for_customer(
        principal.customer, limit=page.limit, offset=page.offset
    )
    return {"items": rows, "limit": page.limit, "offset": page.offset}


def format_addresses_csv(rows: Sequence[Address]) -> str:
    body = "\n".join(f"{r.label},{r.line1},{r.city},{r.postal_code},{r.region}" for r in rows)
    return f"label,line1,city,postal_code,region\n{body}\n"


@router.get("/export")
def export_addresses(db: DbSession, principal: CurrentPrincipal, settings: AppSettings) -> Response:
    rows = AddressRepository(db).list_for_customer(principal.customer, limit=settings.page_size_max)
    return Response(content=format_addresses_csv(rows), media_type="text/csv")


@router.get("/default", response_model=AddressOut)
def get_default_address(db: DbSession, principal: CurrentPrincipal) -> Address:
    address = AddressRepository(db).default_for(principal.customer)
    if address is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no default address")
    return address


@router.get("/{address_id}", response_model=AddressOut)
def get_address(address_id: int, db: DbSession, principal: CurrentPrincipal) -> Address:
    return _load(db, address_id, principal.customer)


@router.post("/{address_id}/default", response_model=AddressOut)
def set_default_address(address_id: int, db: DbSession, principal: CurrentPrincipal) -> Address:
    """Make this address the default for new orders. Returns the updated record."""
    address = _load(db, address_id, principal.customer)
    AddressRepository(db).clear_default(principal.customer)
    address.is_default = True
    return address
