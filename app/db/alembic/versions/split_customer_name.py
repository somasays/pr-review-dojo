"""split customer name into first and last name

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-04 11:15:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("customers") as batch_op:
        batch_op.add_column(
            sa.Column("first_name", sa.String(60), nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("last_name", sa.String(60), nullable=False))
    op.create_index(
        "ix_customers_last_name", "customers", ["last_name", "first_name"], unique=False
    )
    op.create_table(
        "customer_name_backfill_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("original_name", sa.String(120), nullable=False),
        sa.Column("split_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    pass
