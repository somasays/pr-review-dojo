"""customer address book and order shipping address

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-04 10:15:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "addresses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("label", sa.String(length=40), nullable=False),
        sa.Column("line1", sa.String(length=200), nullable=False),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("postal_code", sa.String(length=16), nullable=False),
        sa.Column("region", sa.String(length=8), nullable=False, server_default="US-CA"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_addresses_customer", "addresses", ["customer_id"], unique=False)
    with op.batch_alter_table("orders") as batch:
        batch.add_column(sa.Column("shipping_address_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_orders_shipping_address", "addresses", ["shipping_address_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch:
        batch.drop_constraint("fk_orders_shipping_address", type_="foreignkey")
        batch.drop_column("shipping_address_id")
    op.drop_index("ix_addresses_customer", table_name="addresses")
    op.drop_table("addresses")
