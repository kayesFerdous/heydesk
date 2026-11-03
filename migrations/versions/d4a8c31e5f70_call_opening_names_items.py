"""call opening names items

Revision ID: d4a8c31e5f70
Revises: c7e41d2a9b30
Create Date: 2026-11-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a8c31e5f70'
down_revision: Union[str, Sequence[str], None] = 'c7e41d2a9b30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AGENTS = sa.table("agents", sa.column("id"), sa.column("config", sa.JSON))

OLD = (
    "Hello, this is {business_name} calling about your order {code}, total {total}, "
    "paid in cash on delivery. Shall we confirm it and send it out?"
)
NEW = (
    "Assalamu alaikum, this is {business_name}. You ordered {items}, {total} with "
    "delivery, cash on delivery. Shall we send it out?"
)


def upgrade() -> None:
    """The settings page saves the whole `calls` block, so a shop that never changed the call's
    first line has the old default saved. It gets the new one, which names the items; a line
    the owner wrote is kept."""
    bind = op.get_bind()
    for id_, config in list(bind.execute(sa.select(AGENTS.c.id, AGENTS.c.config))):
        calls = config.get("calls") or {}
        if calls.get("opening") == OLD:
            config = {**config, "calls": {**calls, "opening": NEW}}
            bind.execute(AGENTS.update().where(AGENTS.c.id == id_).values(config=config))


def downgrade() -> None:
    """The old line still works ({code} is still filled in), so it isn't put back."""
