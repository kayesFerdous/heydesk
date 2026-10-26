"""shop orders remember reminders and reviews

Revision ID: 7d2e9b50be90
Revises: 5db55ce78712
Create Date: 2026-10-26 10:28:27.453914

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7d2e9b50be90"
down_revision: Union[str, Sequence[str], None] = "5db55ce78712"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("shop_orders", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("reminded", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("review_asked", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("shop_orders", schema=None) as batch_op:
        batch_op.drop_column("review_asked")
        batch_op.drop_column("reminded")
