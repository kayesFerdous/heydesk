"""roles, audit events, business status

Revision ID: d2b3e4f5a602
Revises: c1a2d3e4f501
Create Date: 2026-10-04 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2b3e4f5a602'
down_revision: Union[str, Sequence[str], None] = 'c1a2d3e4f501'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Existing users become owners; existing businesses are active (D23)."""
    with op.batch_alter_table('workspaces', schema=None) as batch_op:
        batch_op.add_column(sa.Column('status', sa.String(length=10), server_default='active', nullable=False))
        batch_op.create_check_constraint(op.f('ck_workspaces_status'), "status IN ('active', 'suspended')")

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('role', sa.String(length=10), server_default='owner', nullable=False))
        batch_op.alter_column('workspace_id', existing_type=sa.String(length=32), nullable=True)
        batch_op.create_check_constraint(op.f('ck_users_role'), "role IN ('admin', 'owner')")
        batch_op.create_check_constraint(op.f('ck_users_admin_has_no_workspace'), "(role = 'admin') = (workspace_id IS NULL)")

    op.create_table('audit_events',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('actor_id', sa.String(length=32), nullable=False),
    sa.Column('workspace_id', sa.String(length=32), nullable=True),
    sa.Column('action', sa.String(length=120), nullable=False),
    sa.Column('target', sa.String(length=200), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], name=op.f('fk_audit_events_actor_id_users')),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_audit_events_workspace_id_workspaces')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_events'))
    )
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_events_actor_id'), ['actor_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_events_at'), ['at'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_events_workspace_id'), ['workspace_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema (admins, having no business, are deleted)."""
    op.drop_table('audit_events')
    op.execute("DELETE FROM users WHERE role = 'admin'")
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_users_admin_has_no_workspace'), type_='check')
        batch_op.drop_constraint(op.f('ck_users_role'), type_='check')
        batch_op.alter_column('workspace_id', existing_type=sa.String(length=32), nullable=False)
        batch_op.drop_column('role')

    with op.batch_alter_table('workspaces', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_workspaces_status'), type_='check')
        batch_op.drop_column('status')
