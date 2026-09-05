"""rename tracking_number to tracking_id

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-05 09:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The carrier webhook payload calls this field trackingId, so line up
    # the column with the integration before more code reads it.
    with op.batch_alter_table("orders") as batch_op:
        batch_op.alter_column(
            "tracking_number",
            new_column_name="tracking_id",
            existing_type=sa.String(64),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch_op:
        batch_op.alter_column(
            "tracking_id",
            new_column_name="tracking_number",
            existing_type=sa.String(64),
            existing_nullable=False,
        )
