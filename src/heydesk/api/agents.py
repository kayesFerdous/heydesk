"""A business's agent: create it, read it, edit it (design §9). One per business.

Edits are live from the next message. Problems come back per field.
"""

from typing import Annotated, Any

from fastapi import APIRouter, File, UploadFile, status
from pydantic import BaseModel

from heydesk.api.deps import Svc, Workspace
from heydesk.services.agents import AgentInfo
from heydesk.shop.config import ShopConfig

router = APIRouter(tags=["agents"])


class DeskOut(BaseModel):
    """`kind` here and on `AgentOut` is always "shop": the frontend still reads them."""

    kind: str
    label: str
    description: str
    config_schema: dict[str, Any]  # the fields an agent has


class AgentIn(BaseModel):
    config: dict[str, Any] = {}  # its fields; what's left out has a default


class AgentOut(BaseModel):
    kind: str = "shop"
    public_key: str  # the chat URL: /api/chat/{public_key}
    config: dict[str, Any]
    missing: list[str]  # what's still to fill in, in plain words; empty when set up


def agent_out(agent: AgentInfo) -> AgentOut:
    return AgentOut(
        public_key=agent.public_key,
        config=agent.config.model_dump(mode="json"),
        missing=agent.missing,
    )


@router.get("/api/desks")
async def desks() -> list[DeskOut]:
    """What an agent is, for the frontend's setup page and settings form."""
    return [
        DeskOut(
            kind="shop",
            label="Online shop",
            description=(
                "Answers questions about your products and prices, takes cash-on-delivery "
                "orders, and tells buyers where their order is."
            ),
            config_schema=ShopConfig.model_json_schema(),
        )
    ]


@router.post("/api/agent", status_code=status.HTTP_201_CREATED)
async def create(body: AgentIn, services: Svc, workspace_id: Workspace) -> AgentOut:
    await services.agents.create(workspace_id, body.config)
    return agent_out(await services.agents.get(workspace_id))


@router.get("/api/agent")
async def read(services: Svc, workspace_id: Workspace) -> AgentOut:
    return agent_out(await services.agents.get(workspace_id))


class LogoOut(BaseModel):
    url: str  # also saved as the agent's `widget.logo`


LOGO_SIDE = 256  # px: a logo is shown small, in the bubble and the chat's header


@router.post("/api/agent/logo")
async def add_logo(
    file: Annotated[UploadFile, File(description="A JPEG, PNG or WebP picture, up to 10 MB")],
    services: Svc,
    workspace_id: Workspace,
) -> LogoOut:
    """The business's logo for the website chat: saved small and set on the widget."""
    widget = (await services.agents.get(workspace_id)).config.widget  # 404 before anything is saved
    photo = await services.photos.save(workspace_id, "logo", await file.read(), LOGO_SIDE)
    await services.agents.update(
        workspace_id, {"widget": widget.model_copy(update={"logo": photo.url}).model_dump()}
    )
    return LogoOut(url=photo.url)


@router.patch("/api/agent")
async def edit(patch: dict[str, Any], services: Svc, workspace_id: Workspace) -> AgentOut:
    """Top-level fields replace the config's: send the whole `hours`, not a part."""
    await services.agents.update(workspace_id, patch)
    return agent_out(await services.agents.get(workspace_id))
