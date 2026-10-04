"""records: orders and requests in one table

Revision ID: e3c4f5a6b703
Revises: d2b3e4f5a602
Create Date: 2026-10-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3c4f5a6b703'
down_revision: Union[str, Sequence[str], None] = 'd2b3e4f5a602'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Orders and requests become records (D24); nothing is lost. A request gets a number."""
    op.create_table('records',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('workspace_id', sa.String(length=32), nullable=False),
    sa.Column('agent_id', sa.String(length=32), nullable=False),
    sa.Column('conversation_id', sa.String(length=32), nullable=False),
    sa.Column('job', sa.String(length=50), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('customer_name', sa.String(length=100), nullable=False),
    sa.Column('customer_phone', sa.String(length=20), nullable=False),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('note', sa.String(), nullable=False),
    sa.Column('lines', sa.JSON(), nullable=True),
    sa.Column('total', sa.Integer(), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=True),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("kind != 'order' OR (lines IS NOT NULL AND total IS NOT NULL AND due_at IS NOT NULL)", name=op.f('ck_records_order_has_total')),
    sa.CheckConstraint("kind IN ('order', 'request')", name=op.f('ck_records_kind')),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], name=op.f('fk_records_agent_id_agents')),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_records_conversation_id_conversations')),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_records_workspace_id_workspaces')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_records')),
    sa.UniqueConstraint('workspace_id', 'kind', 'number', name=op.f('uq_records_workspace_id_kind_number'))
    )
    with op.batch_alter_table('records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_records_agent_id'), ['agent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_records_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_records_workspace_id'), ['workspace_id'], unique=False)

    op.execute("""
        INSERT INTO records (id, workspace_id, agent_id, conversation_id, job, kind, number, status,
                             customer_name, customer_phone, details, note, lines, total, currency,
                             due_at, created_at, updated_at)
        SELECT id, workspace_id, agent_id, conversation_id, 'order', 'order', number, status,
               customer_name, customer_phone, '{}', note, lines, total, currency,
               pickup_time, created_at, created_at
        FROM orders
    """)
    op.execute("""
        INSERT INTO records (id, workspace_id, agent_id, conversation_id, job, kind, number, status,
                             customer_name, customer_phone, details, note, created_at, updated_at)
        SELECT id, workspace_id, agent_id, conversation_id, task, 'request',
               100 + ROW_NUMBER() OVER (PARTITION BY workspace_id ORDER BY created_at, id), status,
               name, phone, details, '', created_at, created_at
        FROM requests
    """)
    op.drop_table('requests')
    op.drop_table('orders')


def downgrade() -> None:
    """Back to two tables. Orders keep their numbers; requests lose theirs."""
    op.create_table('orders',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('workspace_id', sa.String(length=32), nullable=False),
    sa.Column('agent_id', sa.String(length=32), nullable=False),
    sa.Column('conversation_id', sa.String(length=32), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('lines', sa.JSON(), nullable=False),
    sa.Column('total', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('pickup_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('customer_name', sa.String(length=100), nullable=False),
    sa.Column('customer_phone', sa.String(length=20), nullable=False),
    sa.Column('note', sa.String(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], name=op.f('fk_orders_agent_id_agents')),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_orders_conversation_id_conversations')),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_orders_workspace_id_workspaces')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_orders')),
    sa.UniqueConstraint('agent_id', 'number', name=op.f('uq_orders_agent_id_number'))
    )
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_orders_agent_id'), ['agent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_orders_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_orders_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('requests',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('workspace_id', sa.String(length=32), nullable=False),
    sa.Column('agent_id', sa.String(length=32), nullable=False),
    sa.Column('conversation_id', sa.String(length=32), nullable=False),
    sa.Column('task', sa.String(length=50), nullable=False),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('phone', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], name=op.f('fk_requests_agent_id_agents')),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_requests_conversation_id_conversations')),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_requests_workspace_id_workspaces')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_requests'))
    )
    with op.batch_alter_table('requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_requests_agent_id'), ['agent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_requests_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_requests_workspace_id'), ['workspace_id'], unique=False)

    op.execute("""
        INSERT INTO orders (id, workspace_id, agent_id, conversation_id, number, lines, total,
                            currency, pickup_time, customer_name, customer_phone, note, status,
                            created_at)
        SELECT id, workspace_id, agent_id, conversation_id, number, lines, total, currency, due_at,
               customer_name, customer_phone, note, status, created_at
        FROM records WHERE kind = 'order'
    """)
    op.execute("""
        INSERT INTO requests (id, workspace_id, agent_id, conversation_id, task, details, name,
                              phone, status, created_at)
        SELECT id, workspace_id, agent_id, conversation_id, job, details, customer_name,
               customer_phone, status, created_at
        FROM records WHERE kind = 'request'
    """)
    op.drop_table('records')
