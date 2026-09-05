"""order events audit table

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-05 09:15:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "order_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("from_status", sa.String(32), nullable=True),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(32), nullable=False, server_default="service"),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_order_events_order_occurred", "order_events", ["order_id", "occurred_at"], unique=False
    )

    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True))

    # CREATE INDEX CONCURRENTLY cannot run inside the migration transaction.
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_orders_last_event_at",
            "orders",
            ["last_event_at"],
            unique=False,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    op.drop_index("ix_orders_last_event_at", table_name="orders")
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("last_event_at")
    op.drop_index("ix_order_events_order_occurred", table_name="order_events")
    op.drop_table("order_events")
