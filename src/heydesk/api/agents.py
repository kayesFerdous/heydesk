"""A business's agent: create it as one kind of desk, read it, edit it (design §9). One per business.

Edits are live from the next message. Problems come back per field.
"""

from typing import Annotated, Any

from fastapi import APIRouter, File, UploadFile, status
from pydantic import BaseModel

from heydesk.api.deps import Svc, Workspace
from heydesk.desks import DESKS
from heydesk.services.agents import AgentInfo

router = APIRouter(tags=["agents"])


class DeskOut(BaseModel):
    kind: str
    label: str
    description: str
    config_schema: dict[str, Any]  # the fields an agent of this kind has


class AgentIn(BaseModel):
    kind: str  # shop: see GET /api/desks
    config: dict[str, Any] = {}  # its fields; what's left out has a default


class AgentOut(BaseModel):
    kind: str
    public_key: str  # the chat URL: /api/chat/{public_key}
    config: dict[str, Any]
    missing: list[str]  # what's still to fill in, in plain words; empty when set up


def agent_out(agent: AgentInfo) -> AgentOut:
    return AgentOut(
        kind=agent.kind,
        public_key=agent.public_key,
        config=agent.config.model_dump(mode="json"),
        missing=agent.missing,
    )


@router.get("/api/desks")
async def desks() -> list[DeskOut]:
    """The kinds of business an agent can be."""
    return [
        DeskOut(
            kind=d.kind,
            label=d.label,
            description=d.description,
            config_schema=d.config_model.model_json_schema(),
        )
        for d in DESKS.values()
    ]


@router.post("/api/agent", status_code=status.HTTP_201_CREATED)
async def create(body: AgentIn, services: Svc, ws: Workspace) -> AgentOut:
    await services.agents.create(ws, body.kind, body.config)
    return agent_out(await services.agents.get(ws))


@router.get("/api/agent")
async def read(services: Svc, ws: Workspace) -> AgentOut:
    return agent_out(await services.agents.get(ws))


class LogoOut(BaseModel):
    url: str  # also saved as the agent's `widget.logo`


LOGO_SIDE = 256  # px: a logo is shown small, in the bubble and the chat's header


@router.post("/api/agent/logo")
async def add_logo(
    file: Annotated[UploadFile, File(description="A JPEG, PNG or WebP picture, up to 10 MB")],
    services: Svc,
    ws: Workspace,
) -> LogoOut:
    """The business's logo for the website chat: saved small and set on the widget."""
    widget = (await services.agents.get(ws)).config.widget  # 404 before anything is saved
    photo = await services.photos.save(ws, "logo", await file.read(), LOGO_SIDE)
    await services.agents.update(
        ws, {"widget": widget.model_copy(update={"logo": photo.url}).model_dump()}
    )
    return LogoOut(url=photo.url)


@router.patch("/api/agent")
async def edit(patch: dict[str, Any], services: Svc, ws: Workspace) -> AgentOut:
    """Top-level fields replace the config's: send the whole `hours`, not a part."""
    await services.agents.update(ws, patch)
    return agent_out(await services.agents.get(ws))
