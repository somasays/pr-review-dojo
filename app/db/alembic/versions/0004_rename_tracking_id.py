"""add tracking_id alongside tracking_number

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
    # Expand step: add the column under the name the carrier webhook payload
    # uses without touching tracking_number yet. Pods still running the
    # previous release keep reading and writing tracking_number until they
    # are fully rolled out. A later revision drops tracking_number once
    # nothing reads it anymore.
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(
            sa.Column("tracking_id", sa.String(64), nullable=False, server_default="")
        )


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("tracking_id")
