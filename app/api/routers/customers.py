from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import AdminPrincipal, CurrentPrincipal, DbSession, PageParams
from app.api.schemas import CustomerCreate, CustomerOut, Page
from app.db.models import Customer
from app.db.repositories import CustomerRepository
from app.domain.names import split_full_name

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("/me", response_model=CustomerOut)
def me(db: DbSession, principal: CurrentPrincipal) -> Customer:
    return CustomerRepository(db).get(principal.customer)


@router.get("", response_model=Page[CustomerOut])
def list_customers(db: DbSession, _admin: AdminPrincipal, page: PageParams) -> dict[str, object]:
    rows = CustomerRepository(db).list(limit=page.limit, offset=page.offset)
    return {"items": rows, "limit": page.limit, "offset": page.offset}


@router.post("", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_customer(db: DbSession, _admin: AdminPrincipal, body: CustomerCreate) -> Customer:
    repo = CustomerRepository(db)
    if repo.by_email(body.email) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    first_name, last_name = split_full_name(body.name)
    return repo.add(
        Customer(
            email=body.email,
            name=body.name,
            first_name=first_name,
            last_name=last_name,
            region=body.region,
        )
    )
