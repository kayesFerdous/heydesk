"""A business's agent and its items: create from a preset, read, edit (design §9). One per business.

Edits are live from the next message. Problems come back per field.
"""

from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from heydesk.api.deps import Svc, User, Workspace
from heydesk.api.presets import JobOut
from heydesk.errors import Forbidden
from heydesk.runtime.config import AgentConfig
from heydesk.runtime.items import Item
from heydesk.services.agents import AgentInfo
from heydesk.services.item_import import parse_items

router = APIRouter(tags=["agents"])


# What only an admin may change (D25): the role text comes from the preset. An owner writes
# `notes` for tone and rules, and edits their business. What the agent does is the preset's (D37).
ADMIN_ONLY = ("instructions", "preset")


def owners_may_set(who: User, fields: dict[str, Any]) -> None:
    locked = [name for name in ADMIN_ONLY if name in fields]
    if locked and not who.is_admin:
        raise Forbidden(f"{', '.join(locked)}: only HeyDesk can change that. Use `notes` instead.")


class AgentIn(BaseModel):
    preset: str
    config: dict[str, Any] = {}  # AgentConfig fields; what's left out comes from the preset
    items: list[Item] = []


class AgentOut(BaseModel):
    public_key: str  # the chat URL: /api/chat/{public_key}
    config: AgentConfig
    jobs: list[JobOut]  # what the agent does: its preset's jobs (D37)
    missing: list[str]  # what's still to fill in, in plain words; empty when set up


def out(agent: AgentInfo) -> AgentOut:
    return AgentOut(
        public_key=agent.public_key,
        config=agent.config,
        jobs=[
            JobOut(id=i, label=j.label, description=j.description) for i, j in agent.jobs.items()
        ],
        missing=agent.missing,
    )


@router.post("/api/agent", status_code=status.HTTP_201_CREATED)
async def create(body: AgentIn, who: User, services: Svc, ws: Workspace) -> AgentOut:
    owners_may_set(who, body.config)
    agent_id = await services.agents.create_from_preset(ws, body.preset, body.config)
    await services.items.replace(ws, agent_id, body.items)
    return out(await services.agents.get(ws))


@router.get("/api/agent")
async def read(services: Svc, ws: Workspace) -> AgentOut:
    return out(await services.agents.get(ws))


@router.patch("/api/agent")
async def edit(patch: dict[str, Any], who: User, services: Svc, ws: Workspace) -> AgentOut:
    """Top-level fields replace the config's: send the whole `knowledge`, not a part.
    An owner can't change `instructions` (403): they write `notes`."""
    owners_may_set(who, patch)
    await services.agents.update(ws, patch)
    return out(await services.agents.get(ws))


@router.get("/api/agent/items")
async def items(services: Svc, ws: Workspace) -> list[Item]:
    agent = await services.agents.get(ws)  # 404 until the business has an agent
    return await services.items.all(ws, agent.id)


@router.put("/api/agent/items")
async def replace_items(body: list[Item], services: Svc, ws: Workspace) -> list[Item]:
    """The agent's items become exactly these, in this order. Live from the next message."""
    agent = await services.agents.get(ws)
    await services.items.replace(ws, agent.id, body)
    return body


class ImportIn(BaseModel):
    csv: str = Field(
        max_length=2_000_000, description="The file's text: a header row, then one item per row"
    )
    mapping: dict[str, str] = Field(
        default={}, description='Your header → ours: {"Product Title": "name", "MRP": "price"}'
    )
    dry_run: bool = Field(default=False, description="Check the file and show what would be saved")


class ImportOut(BaseModel):
    saved: bool
    count: int
    attributes: list[str]  # the columns that became the items' own facts
    preview: list[Item]  # the first few items, as they'll be saved


@router.post("/api/agent/items/import")
async def import_items(body: ImportIn, services: Svc, ws: Workspace) -> ImportOut:
    """Replaces the agent's items with the CSV's. Known columns (name, price, category, tags,
    description, code, available) are the item's fields; every other column becomes a fact.
    Problems come back per row and nothing is saved until the file is clean."""
    agent = await services.agents.get(ws)
    items, attributes = parse_items(body.csv, body.mapping)
    if not body.dry_run:
        await services.items.replace(ws, agent.id, items)
    return ImportOut(
        saved=not body.dry_run, count=len(items), attributes=attributes, preview=items[:5]
    )
