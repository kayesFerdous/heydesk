"""coupons

Revision ID: 5db55ce78712
Revises: 6155488d3e4a
Create Date: 2026-10-26 10:19:28.300266

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "5db55ce78712"
down_revision: Union[str, Sequence[str], None] = "6155488d3e4a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "coupons",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_coupons_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coupons")),
        sa.UniqueConstraint("workspace_id", "code", name=op.f("uq_coupons_workspace_id_code")),
    )
    with op.batch_alter_table("coupons", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_coupons_workspace_id"), ["workspace_id"], unique=False)

    with op.batch_alter_table("shop_orders", schema=None) as batch_op:
        batch_op.add_column(sa.Column("discount", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(
            sa.Column("coupon", sa.String(length=30), nullable=False, server_default="")
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("shop_orders", schema=None) as batch_op:
        batch_op.drop_column("coupon")
        batch_op.drop_column("discount")

    with op.batch_alter_table("coupons", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_coupons_workspace_id"))

    op.drop_table("coupons")
