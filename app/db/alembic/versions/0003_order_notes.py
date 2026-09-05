"""order notes

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-02 11:15:00
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
        "order_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("author", sa.String(64), nullable=False),
        sa.Column("body", sa.String(500), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_order_notes_order", "order_notes", ["order_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_order_notes_order", table_name="order_notes")
    op.drop_table("order_notes")
