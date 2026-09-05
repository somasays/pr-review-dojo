"""index customers by region and name

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-04 09:00:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_customers_region_name", "customers", ["region", "name"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_customers_region_name", table_name="customers")
