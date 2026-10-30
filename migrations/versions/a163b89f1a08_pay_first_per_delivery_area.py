"""pay first per delivery area

Revision ID: a163b89f1a08
Revises: 6fbc01248f23
Create Date: 2026-10-21 16:10:44.671397

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a163b89f1a08'
down_revision: Union[str, Sequence[str], None] = '6fbc01248f23'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AGENTS = sa.table("agents", sa.column("id"), sa.column("kind"), sa.column("config", sa.JSON))


def _shops() -> list[tuple[str, dict]]:
    rows = op.get_bind().execute(sa.select(AGENTS.c.id, AGENTS.c.config).where(AGENTS.c.kind == "shop"))
    return [(id_, dict(config)) for id_, config in rows]


def _save(id_: str, config: dict) -> None:
    op.get_bind().execute(AGENTS.update().where(AGENTS.c.id == id_).values(config=config))


def upgrade() -> None:
    """A shop's `pay_first` moves onto each delivery area: one that paid first still does,
    in every area."""
    for id_, config in _shops():
        if "pay_first" not in config:
            continue
        on = config.pop("pay_first")
        if on:
            config["delivery"] = [{**zone, "pay_first": True} for zone in config.get("delivery", [])]
        _save(id_, config)


def downgrade() -> None:
    """Back to one switch: on if any area paid first."""
    for id_, config in _shops():
        zones = config.get("delivery", [])
        if not any("pay_first" in zone for zone in zones) and "call_to_confirm" not in config:
            continue
        config["pay_first"] = any([zone.pop("pay_first", False) for zone in zones])
        config.pop("call_to_confirm", None)
        _save(id_, config)
