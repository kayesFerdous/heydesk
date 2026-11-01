"""drop practice mode

Revision ID: c7e41d2a9b30
Revises: 9751b2606b9e
Create Date: 2026-11-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e41d2a9b30'
down_revision: Union[str, Sequence[str], None] = '9751b2606b9e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AGENTS = sa.table("agents", sa.column("id"), sa.column("config", sa.JSON))


def upgrade() -> None:
    """An agent's config no longer has `practice` or `testers` (the config refuses fields it
    doesn't know), so they are taken out of the saved ones."""
    bind = op.get_bind()
    for id_, config in list(bind.execute(sa.select(AGENTS.c.id, AGENTS.c.config))):
        config = dict(config)
        if "practice" in config or "testers" in config:
            config.pop("practice", None)
            config.pop("testers", None)
            bind.execute(AGENTS.update().where(AGENTS.c.id == id_).values(config=config))


def downgrade() -> None:
    """Nothing to put back: the settings are gone."""
