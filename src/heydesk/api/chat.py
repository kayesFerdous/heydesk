from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from heydesk.api.deps import Svc
from heydesk.api.sse import to_sse

router = APIRouter()


class AgentOut(BaseModel):
    name: str
    greeting: str  # the page shows it on load; the first turn stores it (D8)


class ChatIn(BaseModel):
    message: str
    conversation_id: str | None = None  # none: start a new conversation


@router.get("/api/chat/{public_key}")
async def agent(public_key: str, services: Svc) -> AgentOut:
    found = await services.agents.by_key(public_key)  # 404 if unknown
    return AgentOut(name=found.config.name, greeting=found.greeting)


@router.post("/api/chat/{public_key}")
async def chat(public_key: str, body: ChatIn, services: Svc) -> StreamingResponse:
    agent = await services.agents.by_key(public_key)  # 404 if unknown
    turn = await services.conversations.start_turn(
        agent, body.conversation_id, body.message, channel="web"
    )
    return StreamingResponse(
        to_sse(turn),
        media_type="text/event-stream",
        # no-transform: a proxy (the frontend's dev server, ngrok) mustn't compress the stream,
        # or it holds the words back and the reply arrives cut off
        headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"},
    )


class TeamOut(BaseModel):
    replies: list[str]  # oldest first; the page shows the ones it hasn't yet


@router.get("/api/chat/{public_key}/{conversation_id}/team")
async def team_replies(public_key: str, conversation_id: str, services: Svc) -> TeamOut:
    """What the team wrote from Telegram (D43). The chat page asks every few seconds."""
    agent = await services.agents.by_key(public_key)  # 404 if unknown
    return TeamOut(replies=await services.conversations.team_replies(agent, conversation_id))
