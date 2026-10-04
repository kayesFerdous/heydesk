"""The presets a business can start an agent from (design §9)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from heydesk.api.deps import get_services
from heydesk.presets import load_jobs
from heydesk.services import Services

router = APIRouter(tags=["presets"])


class PresetOut(BaseModel):
    id: str
    label: str
    description: str
    greeting: str
    instructions: str
    jobs: list[str]  # the jobs it starts with
    items_label: str
    knowledge_schema: dict[str, Any]  # what `knowledge` must look like


@router.get("/api/presets")
async def presets(services: Annotated[Services, Depends(get_services)]) -> list[PresetOut]:
    return [
        PresetOut(
            id=p.id,
            label=p.label,
            description=p.description,
            greeting=p.greeting,
            instructions=p.instructions,
            jobs=p.jobs,
            items_label=p.items_label,
            knowledge_schema=p.knowledge_model.model_json_schema(),
        )
        for p in services.agents.presets.values()
    ]


class JobOut(BaseModel):
    id: str
    label: str
    description: str  # for the owner, in plain words


@router.get("/api/jobs")
async def jobs() -> list[JobOut]:
    """What an agent can be set up to do. An owner switches them on with `PATCH /api/agent`."""
    return [JobOut(id=i, label=j.label, description=j.description) for i, j in load_jobs().items()]
