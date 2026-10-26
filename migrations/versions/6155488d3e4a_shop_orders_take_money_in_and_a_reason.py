"""shop orders take money in and a reason

Revision ID: 6155488d3e4a
Revises: 69a59358f78f
Create Date: 2026-10-26 09:43:47.521751

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6155488d3e4a"
down_revision: Union[str, Sequence[str], None] = "69a59358f78f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("shop_orders", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("paid_in", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("reason", sa.String(length=200), nullable=False, server_default="")
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("shop_orders", schema=None) as batch_op:
        batch_op.drop_column("reason")
        batch_op.drop_column("paid_in")
