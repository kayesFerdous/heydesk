"""item attributes

Revision ID: f4d5a6b7c804
Revises: e3c4f5a6b703
Create Date: 2026-10-04 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4d5a6b7c804'
down_revision: Union[str, Sequence[str], None] = 'e3c4f5a6b703'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Items get their own facts (D27); existing items have none."""
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('attributes', sa.JSON(), server_default='{}', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.drop_column('attributes')
