"""tickets for complaints

Revision ID: 12bac5f512ab
Revises: 3bbc28fefb9b
Create Date: 2026-10-26 10:51:27.189064

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '12bac5f512ab'
down_revision: Union[str, Sequence[str], None] = '3bbc28fefb9b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('tickets',
    sa.Column('reason', sa.String(length=300), nullable=False),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('workspace_id', sa.String(length=32), nullable=False),
    sa.Column('conversation_id', sa.String(length=32), nullable=True),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('phone', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_tickets_conversation_id_conversations')),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_tickets_workspace_id_workspaces')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tickets')),
    sa.UniqueConstraint('workspace_id', 'number', name=op.f('uq_tickets_workspace_id_number'))
    )
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tickets_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_tickets_workspace_id'), ['workspace_id'], unique=False)



def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tickets_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_tickets_conversation_id'))

    op.drop_table('tickets')
