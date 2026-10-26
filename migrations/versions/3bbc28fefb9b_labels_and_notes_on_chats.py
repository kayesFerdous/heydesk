"""labels and notes on chats

Revision ID: 3bbc28fefb9b
Revises: 238d3fd32dca
Create Date: 2026-10-26 10:39:14.371831

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3bbc28fefb9b"
down_revision: Union[str, Sequence[str], None] = "238d3fd32dca"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "notes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("conversation_id", sa.String(length=32), nullable=False),
        sa.Column("author", sa.String(length=320), nullable=False),
        sa.Column("text", sa.String(length=2000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_notes_conversation_id_conversations"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_notes_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notes")),
    )
    with op.batch_alter_table("notes", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_notes_conversation_id"), ["conversation_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_notes_workspace_id"), ["workspace_id"], unique=False)

    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("labels", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.drop_column("labels")

    with op.batch_alter_table("notes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notes_workspace_id"))
        batch_op.drop_index(batch_op.f("ix_notes_conversation_id"))

    op.drop_table("notes")
