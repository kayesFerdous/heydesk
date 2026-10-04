"""one agent per business

Revision ID: c1a2d3e4f501
Revises: 8f5d4b781702
Create Date: 2026-10-04 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c1a2d3e4f501"
down_revision: Union[str, Sequence[str], None] = "8f5d4b781702"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """A workspace may have only one agent (D22). Fails if one already has several."""
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_agents_workspace_id"))
        batch_op.create_unique_constraint(batch_op.f("uq_agents_workspace_id"), ["workspace_id"])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("uq_agents_workspace_id"), type_="unique")
        batch_op.create_index(batch_op.f("ix_agents_workspace_id"), ["workspace_id"], unique=False)
