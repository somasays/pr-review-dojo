"""order events audit table

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-05 09:15:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.db.models import Order, OrderEvent

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ORDER_STATUSES = (
    "draft",
    "pending_payment",
    "paid",
    "shipped",
    "delivered",
    "cancelled",
    "refunded",
)


def upgrade() -> None:
    op.create_table(
        "order_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("from_status", sa.String(32), nullable=True),
        sa.Column(
            "to_status", sa.Enum(*ORDER_STATUSES, name="order_status"), nullable=False
        ),
        sa.Column("actor", sa.String(32), nullable=False, server_default="service"),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # Give the order key room before order_events outgrows it.
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.alter_column("id", type_=sa.BigInteger())
    with op.batch_alter_table("order_items") as batch_op:
        batch_op.alter_column("order_id", type_=sa.BigInteger())

    bind = op.get_bind()
    existing = bind.execute(sa.select(Order.id, Order.status, Order.created_at)).all()
    if existing:
        op.bulk_insert(
            OrderEvent.__table__,
            [
                {
                    "order_id": order_id,
                    "from_status": None,
                    "to_status": status,
                    "actor": "backfill",
                    "occurred_at": created_at,
                }
                for order_id, status, created_at in existing
            ],
        )
        op.execute(
            sa.text("UPDATE orders SET last_event_at = created_at WHERE last_event_at IS NULL")
        )

    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("updated_at")

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
    op.drop_table("order_events")
    with op.batch_alter_table("order_items") as batch_op:
        batch_op.alter_column("order_id", type_=sa.Integer())
    with op.batch_alter_table("orders") as batch_op:
        batch_op.alter_column("id", type_=sa.Integer())
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
        batch_op.drop_column("discount_code")
