"""discount codes table

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
        "discount_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("value", sa.Numeric(12, 2), nullable=False),
        sa.Column("min_subtotal", sa.Numeric(12, 2), nullable=True),
        sa.Column("max_redemptions", sa.Integer(), nullable=True),
        sa.Column("times_redeemed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("discount_codes")
