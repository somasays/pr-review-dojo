"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-01 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("region", sa.String(8), nullable=False, server_default="US-CA"),
        sa.Column("api_key_hash", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sku", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("stock", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("discount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("tax", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("discount_code", sa.String(32), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("customer_id", "idempotency_key", name="uq_orders_customer_idem"),
    )
    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("products")
    op.drop_table("customers")
