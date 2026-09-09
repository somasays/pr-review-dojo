"""gift cards

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-07 10:00:00
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
        "gift_cards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
    )
    op.add_column("orders", sa.Column("gift_card_code", sa.String(32), nullable=True))
    op.add_column(
        "orders",
        sa.Column("gift_card_redeemed", sa.Numeric(12, 2), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("orders", "gift_card_redeemed")
    op.drop_column("orders", "gift_card_code")
    op.drop_table("gift_cards")
