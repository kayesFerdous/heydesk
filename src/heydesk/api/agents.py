"""A business's agent and its items: create from a preset, read, edit (design §9). One per business.

Edits are live from the next message. Problems come back per field.
"""

from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel

from heydesk.api.deps import Svc, User, Workspace
from heydesk.errors import Forbidden
from heydesk.runtime.config import AgentConfig
from heydesk.runtime.items import Item
from heydesk.services.agents import AgentInfo

router = APIRouter(tags=["agents"])


# What only an admin may change (D25): the role text and the tools come from the preset.
# An owner writes `notes` for tone and rules, and edits everything about their business.
ADMIN_ONLY = ("instructions", "tools", "preset")


def owners_may_set(who: User, fields: dict[str, Any]) -> None:
    locked = [name for name in ADMIN_ONLY if name in fields]
    if locked and not who.is_admin:
        raise Forbidden(f"{', '.join(locked)}: only HeyDesk can change that. Use `notes` instead.")


class AgentIn(BaseModel):
    preset: str
    config: dict[str, Any]  # AgentConfig fields; what's left out comes from the preset
    items: list[Item] = []


class AgentOut(BaseModel):
    public_key: str  # the chat URL: /api/chat/{public_key}
    config: AgentConfig


def out(agent: AgentInfo) -> AgentOut:
    return AgentOut(public_key=agent.public_key, config=agent.config)


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
    An owner can't change `instructions` or `tools` (403): they write `notes`."""
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
