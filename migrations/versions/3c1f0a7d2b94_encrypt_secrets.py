"""encrypt the secrets businesses give us

Revision ID: 3c1f0a7d2b94
Revises: e056df2c394f
Create Date: 2026-10-15 21:00:00

Bot and Page tokens and SSLCommerz store passwords were saved as given. This widens the two
columns for their encrypted form and encrypts the rows already there with SECRETS_KEY.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from heydesk.config import Settings
from heydesk.db import secret_box


# revision identifiers, used by Alembic.
revision: str = '3c1f0a7d2b94'
down_revision: Union[str, Sequence[str], None] = 'e056df2c394f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SECRETS = [("channels", "token", 500), ("payment_accounts", "password", 200)]


def upgrade() -> None:
    box = secret_box(Settings().secrets_key)
    for table, column, _ in SECRETS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, type_=sa.String(length=1000))
        _rewrite(table, column, lambda plain: box.encrypt(plain.encode()).decode())


def downgrade() -> None:
    box = secret_box(Settings().secrets_key)
    for table, column, length in SECRETS:
        _rewrite(table, column, lambda sealed: box.decrypt(sealed.encode()).decode())
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, type_=sa.String(length=length))


def _rewrite(table: str, column: str, change) -> None:
    db = op.get_bind()
    rows = db.execute(sa.text(f"SELECT id, {column} FROM {table}")).all()
    for row_id, value in rows:
        db.execute(
            sa.text(f"UPDATE {table} SET {column} = :value WHERE id = :id"),
            {"value": change(value), "id": row_id},
        )
