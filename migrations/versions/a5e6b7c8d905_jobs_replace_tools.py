"""jobs replace tools

Revision ID: a5e6b7c8d905
Revises: f4d5a6b7c804
Create Date: 2026-10-04 15:00:00.000000

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a5e6b7c8d905'
down_revision: Union[str, Sequence[str], None] = 'f4d5a6b7c804'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _jobs_from(preset: str, tools: list[str]) -> list[str]:
    """The jobs an agent with these tools was doing (D32)."""
    jobs = []
    if "place_order" in tools and preset == "restaurant":
        jobs.append("order")
    if "submit_request" in tools:
        jobs += ["appointment", "message"] if preset == "clinic" else ["message"]
    return jobs


def _tools_from(jobs: list[str]) -> list[str]:
    tools = ["update_order"] if "order" in jobs else []
    tools += ["search_items", "set_details"]
    if "order" in jobs:
        tools.append("place_order")
    if "appointment" in jobs or "message" in jobs:
        tools.append("submit_request")
    return tools + ["handoff_to_human"]


def upgrade() -> None:
    """An agent's `tools` list becomes the `jobs` it was doing; nothing else changes."""
    conn = op.get_bind()
    for agent_id, preset, raw in conn.execute(sa.text("SELECT id, preset, config FROM agents")).all():
        config = json.loads(raw) if isinstance(raw, str) else raw
        config["jobs"] = _jobs_from(preset, config.pop("tools", []))
        conn.execute(
            sa.text("UPDATE agents SET config = :config WHERE id = :id"),
            {"config": json.dumps(config), "id": agent_id},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for agent_id, raw in conn.execute(sa.text("SELECT id, config FROM agents")).all():
        config = json.loads(raw) if isinstance(raw, str) else raw
        config["tools"] = _tools_from(config.pop("jobs", []))
        conn.execute(
            sa.text("UPDATE agents SET config = :config WHERE id = :id"),
            {"config": json.dumps(config), "id": agent_id},
        )
