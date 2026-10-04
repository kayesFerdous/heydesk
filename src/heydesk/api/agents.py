"""A business's agent and its items: create from a preset, read, edit (design §9). One per business.

Edits are live from the next message. Problems come back per field.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from heydesk.api.deps import current_workspace, get_services
from heydesk.runtime.config import AgentConfig
from heydesk.runtime.items import Item
from heydesk.services import Services
from heydesk.services.agents import AgentInfo

router = APIRouter(tags=["agents"])

type Svc = Annotated[Services, Depends(get_services)]
type Workspace = Annotated[str, Depends(current_workspace)]


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
async def create(body: AgentIn, services: Svc, ws: Workspace) -> AgentOut:
    agent_id = await services.agents.create_from_preset(ws, body.preset, body.config)
    await services.items.replace(ws, agent_id, body.items)
    return out(await services.agents.get(ws))


@router.get("/api/agent")
async def read(services: Svc, ws: Workspace) -> AgentOut:
    return out(await services.agents.get(ws))


@router.patch("/api/agent")
async def edit(patch: dict[str, Any], services: Svc, ws: Workspace) -> AgentOut:
    """Top-level fields replace the config's: send the whole `knowledge`, not a part."""
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
