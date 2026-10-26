"""a chat taken over goes back when quiet

Revision ID: 238d3fd32dca
Revises: 7d2e9b50be90
Create Date: 2026-10-26 10:33:21.197511

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '238d3fd32dca'
down_revision: Union[str, Sequence[str], None] = '7d2e9b50be90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('taken_over_at', sa.DateTime(timezone=True), nullable=True))



def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.drop_column('taken_over_at')

