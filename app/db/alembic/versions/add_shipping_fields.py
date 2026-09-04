"""add shipping fields to orders

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
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("shipped_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("tracking_number", sa.String(64), nullable=False, server_default="")
        )


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("shipped_at")
        batch_op.drop_column("tracking_number")
