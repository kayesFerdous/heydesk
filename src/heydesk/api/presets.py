"""The presets a business can start an agent from (design §9)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from heydesk.api.deps import get_services
from heydesk.services import Services
from heydesk.services.agents import preset_jobs

router = APIRouter(tags=["presets"])


class JobOut(BaseModel):
    id: str
    label: str
    description: str  # for the owner, in plain words


class PresetOut(BaseModel):
    id: str
    label: str
    description: str
    greeting: str
    instructions: str
    jobs: list[JobOut]  # what an agent of this kind does (D37)
    tools: list[str]  # its own tools, on top of what the jobs need
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
            jobs=[
                JobOut(id=i, label=j.label, description=j.description)
                for i, j in preset_jobs(p).items()
            ],
            tools=p.tools,
            items_label=p.items_label,
            knowledge_schema=p.knowledge_model.model_json_schema(),
        )
        for p in services.agents.presets.values()
    ]
