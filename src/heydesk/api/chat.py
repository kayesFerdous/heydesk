from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from heydesk.api.deps import get_services
from heydesk.api.sse import to_sse
from heydesk.services import Services

router = APIRouter()


class AgentOut(BaseModel):
    name: str
    greeting: str  # the page shows it on load; the first turn stores it (D8)


class ChatIn(BaseModel):
    message: str
    conversation_id: str | None = None  # none: start a new conversation


@router.get("/api/chat/{public_key}")
async def agent(public_key: str, services: Annotated[Services, Depends(get_services)]) -> AgentOut:
    found = await services.agents.by_key(public_key)  # 404 if unknown
    return AgentOut(name=found.config.name, greeting=found.greeting)


@router.post("/api/chat/{public_key}")
async def chat(
    public_key: str, body: ChatIn, services: Annotated[Services, Depends(get_services)]
) -> StreamingResponse:
    agent = await services.agents.by_key(public_key)  # 404 if unknown
    turn = await services.conversations.start_turn(
        agent, body.conversation_id, body.message, channel="web"
    )
    return StreamingResponse(
        to_sse(turn),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
