"""split customer name into first and last name

Revision ID: 0003
Revises: 0002
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
        batch_op.add_column(
            sa.Column("last_name", sa.String(60), nullable=False, server_default="")
        )
    op.create_table(
        "customer_name_backfill_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("original_name", sa.String(120), nullable=False),
        sa.Column(
            "split_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_customer_name_backfill_log_customer_id",
        "customer_name_backfill_log",
        ["customer_id"],
        unique=False,
    )
    # customers is large and takes writes all day, so the index is built
    # without holding a write lock. CREATE INDEX CONCURRENTLY cannot run
    # inside the migration transaction that env.py opens.
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_customers_last_name_first_name",
            "customers",
            ["last_name", "first_name"],
            unique=False,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_customers_last_name_first_name",
            table_name="customers",
            postgresql_concurrently=True,
        )
    op.drop_index(
        "ix_customer_name_backfill_log_customer_id", table_name="customer_name_backfill_log"
    )
    op.drop_table("customer_name_backfill_log")
    with op.batch_alter_table("customers") as batch_op:
        batch_op.drop_column("last_name")
        batch_op.drop_column("first_name")
