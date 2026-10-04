"""documents

Revision ID: b6f7c8d9ea06
Revises: a5e6b7c8d905
Create Date: 2026-10-04 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6f7c8d9ea06'
down_revision: Union[str, Sequence[str], None] = 'a5e6b7c8d905'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """A business's documents (D34): the text lives here, the chunks in Qdrant."""
    op.create_table('documents',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('workspace_id', sa.String(length=32), nullable=False),
    sa.Column('agent_id', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('chunk_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], name=op.f('fk_documents_agent_id_agents')),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_documents_workspace_id_workspaces')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_documents'))
    )
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_documents_agent_id'), ['agent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_documents_workspace_id'), ['workspace_id'], unique=False)


def downgrade() -> None:
    op.drop_table('documents')
