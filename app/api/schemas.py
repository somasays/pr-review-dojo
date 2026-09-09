"""Pydantic request and response models.

Response models are explicit allowlists. Never return ORM rows directly.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class CustomerCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    region: str = Field(default="US-CA", pattern=r"^[A-Z]{2}(-[A-Z]{2})?$")


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    name: str
    first_name: str
    last_name: str
    region: str
    created_at: datetime


class OrderItemIn(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0, le=1000)


class OrderCreate(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=64)
    items: list[OrderItemIn] = Field(min_length=1, max_length=50)
    discount_codes: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("items")
    @classmethod
    def unique_skus(cls, items: list[OrderItemIn]) -> list[OrderItemIn]:
        skus = [i.sku for i in items]
        if len(skus) != len(set(skus)):
            raise ValueError("duplicate sku in items")
        return items


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sku: str
    quantity: int
    unit_price: Decimal


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    currency: str
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    total: Decimal
    discount_code: str | None
    created_at: datetime
    items: list[OrderItemOut]


class Page[T](BaseModel):
    items: list[T]
    limit: int
    offset: int


class StatusCount(BaseModel):
    status: str
    count: int


class ErrorOut(BaseModel):
    detail: str
