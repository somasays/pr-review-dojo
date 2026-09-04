"""index orders by customer and created_at

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-15 09:30:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_orders_customer_created", "orders", ["customer_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_orders_customer_created", table_name="orders")
