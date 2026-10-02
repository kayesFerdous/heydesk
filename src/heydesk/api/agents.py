"""A business's agents and their items: create from a preset, read, edit (design §9).

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
    id: str
    public_key: str  # the chat URL: /api/chat/{public_key}
    config: AgentConfig


def out(agent: AgentInfo) -> AgentOut:
    return AgentOut(id=agent.id, public_key=agent.public_key, config=agent.config)


@router.post("/api/agents", status_code=status.HTTP_201_CREATED)
async def create(body: AgentIn, services: Svc, ws: Workspace) -> AgentOut:
    agent_id = await services.agents.create_from_preset(ws, body.preset, body.config)
    await services.items.replace(ws, agent_id, body.items)
    return out(await services.agents.get(ws, agent_id))


@router.get("/api/agents")
async def list_agents(services: Svc, ws: Workspace) -> list[AgentOut]:
    return [out(agent) for agent in await services.agents.list(ws)]


@router.get("/api/agents/{agent_id}")
async def read(agent_id: str, services: Svc, ws: Workspace) -> AgentOut:
    return out(await services.agents.get(ws, agent_id))


@router.patch("/api/agents/{agent_id}")
async def edit(agent_id: str, patch: dict[str, Any], services: Svc, ws: Workspace) -> AgentOut:
    """Top-level fields replace the config's: send the whole `knowledge`, not a part."""
    await services.agents.update(ws, agent_id, patch)
    return out(await services.agents.get(ws, agent_id))


@router.get("/api/agents/{agent_id}/items")
async def items(agent_id: str, services: Svc, ws: Workspace) -> list[Item]:
    await services.agents.get(ws, agent_id)  # 404 if it isn't this workspace's
    return await services.items.all(ws, agent_id)


@router.put("/api/agents/{agent_id}/items")
async def replace_items(
    agent_id: str, body: list[Item], services: Svc, ws: Workspace
) -> list[Item]:
    """The agent's items become exactly these, in this order. Live from the next message."""
    await services.agents.get(ws, agent_id)  # 404 if it isn't this workspace's
    await services.items.replace(ws, agent_id, body)
    return body
