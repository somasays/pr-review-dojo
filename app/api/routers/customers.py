from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import AdminPrincipal, CurrentPrincipal, DbSession, PageParams
from app.api.schemas import AddressIn, CustomerCreate, CustomerOut, CustomerSearchPage, Page
from app.db.models import Customer, CustomerAddress
from app.db.repositories import CustomerRepository

router = APIRouter(prefix="/customers", tags=["customers"])

# The console sends `q` for the name box and one `region` per checked box.
SearchPrefix = Annotated[str, Query(alias="q", min_length=1, max_length=120)]
RegionFilter = Annotated[list[str] | None, Query(alias="region")]


def _clean_regions(regions: list[str] | None) -> list[str]:
    """Trim the region codes and drop blanks and duplicates, keeping order."""
    seen: list[str] = []
    for raw in regions or []:
        code = raw.strip().upper()
        if code and code not in seen:
            seen.append(code)
    return seen


@router.get("/me", response_model=CustomerOut)
def me(db: DbSession, principal: CurrentPrincipal) -> Customer:
    return CustomerRepository(db).get(principal.customer)


@router.get("/search", response_model=CustomerSearchPage)
def search_customers(
    db: DbSession,
    _admin: AdminPrincipal,
    page: PageParams,
    q: SearchPrefix,
    region: RegionFilter = None,
) -> dict[str, object]:
    """Find customers by the start of their name, optionally within regions.

    Support uses this to pull up an account from a half-remembered name. With
    no `region` checkbox ticked the search covers every region.
    """
    repo = CustomerRepository(db)
    regions = _clean_regions(region)
    rows = repo.search(q, regions, limit=page.limit, offset=page.offset)
    total = repo.search_count(q, regions)
    return {
        "items": rows,
        "total": total,
        "limit": page.limit,
        "offset": page.offset,
    }


@router.get("/lookup", response_model=CustomerOut)
def lookup_customer(db: DbSession, _admin: AdminPrincipal, q: SearchPrefix) -> Customer:
    """Jump straight to a customer when the console only needs one hit."""
    return CustomerRepository(db).first_match(q)


@router.get("", response_model=Page[CustomerOut])
def list_customers(db: DbSession, _admin: AdminPrincipal, page: PageParams) -> dict[str, object]:
    rows = CustomerRepository(db).list(limit=page.limit, offset=page.offset)
    return {"items": rows, "limit": page.limit, "offset": page.offset}


@router.post("", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_customer(db: DbSession, _admin: AdminPrincipal, body: CustomerCreate) -> Customer:
    repo = CustomerRepository(db)
    if repo.by_email(body.email) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    return repo.add(Customer(email=body.email, name=body.name, region=body.region))


@router.post("/{customer_id}/address")
def set_default_address(
    db: DbSession, _admin: AdminPrincipal, customer_id: int, body: AddressIn
) -> dict[str, object]:
    """Add an address for the customer and make it their default shipping address."""
    repo = CustomerRepository(db)
    repo.get(customer_id)
    address = repo.get_default_address(customer_id, CustomerAddress(line1=body.line1))
    return {"id": address.id, "line1": address.line1}


@router.post("/import")
def import_customers(
    db: DbSession, _admin: AdminPrincipal, rows: list[CustomerCreate]
) -> list[str]:
    """Bulk-add customers from the console's CSV upload; returns the skipped emails."""
    customers = [Customer(email=r.email, name=r.name, region=r.region) for r in rows]
    return CustomerRepository(db).import_many(customers)
