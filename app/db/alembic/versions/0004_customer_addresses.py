"""add customer addresses

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-05 09:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customer_addresses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("line1", sa.String(200), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "uq_customer_default_address",
        "customer_addresses",
        ["customer_id"],
        unique=True,
        sqlite_where=sa.text("is_default"),
    )


def downgrade() -> None:
    op.drop_index("uq_customer_default_address", table_name="customer_addresses")
    op.drop_table("customer_addresses")
