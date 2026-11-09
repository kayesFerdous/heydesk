import base64
import binascii

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from heydesk import shop
from heydesk.api.deps import ClientIp, Svc
from heydesk.api.sse import to_sse
from heydesk.errors import UnreadablePhoto
from heydesk.runtime.config import Widget

router = APIRouter()


class AgentOut(BaseModel):
    name: str
    greeting: str  # the page shows it on load; the first turn stores it
    photos: bool  # the visitor can send a photo: the page shows its button
    voice: bool  # the visitor can call it: the page shows its Call button
    widget: Widget  # how the chat looks on the business's website


MAX_PHOTO = 2_000_000  # bytes: the page makes a photo smaller before it sends it


class ChatIn(BaseModel):
    message: str = ""  # may be empty when there's a photo
    conversation_id: str | None = None  # none: start a new conversation
    # A random token the page keeps in the browser, so the limits know the visitor
    visitor_id: str = Field(min_length=8, max_length=64)
    # A JPEG or PNG in base64, sent with the message. Only an agent with `photos` takes one
    photo: str | None = Field(default=None, max_length=MAX_PHOTO * 4 // 3 + 4)


@router.get("/api/chat/{public_key}")
async def agent(public_key: str, services: Svc) -> AgentOut:
    found = await services.agents.by_key(public_key)  # 404 if unknown
    config = found.config
    widget = config.widget.model_copy(
        update={
            "nudge": config.fill(config.widget.nudge),
            "starters": [config.fill(s) for s in config.widget.starters],
        }
    )
    return AgentOut(
        name=config.name,
        greeting=found.greeting,
        photos=True,
        voice=shop.TAKES_VOICE,
        widget=widget,
    )


@router.post("/api/chat/{public_key}")
async def chat(public_key: str, body: ChatIn, services: Svc, ip: ClientIp) -> StreamingResponse:
    await services.rate_limits.chat(body.visitor_id, ip)  # 429 for a burst
    agent = await services.agents.by_key(public_key)  # 404 if unknown
    turn = await services.conversations.start_turn(
        agent,
        body.conversation_id,
        body.message,
        "web",
        f"web:{body.visitor_id}",
        body.visitor_id,  # the browser is the contact: a returning visitor is known
        photo=decoded(body.photo) if body.photo else None,
    )
    return StreamingResponse(
        to_sse(turn),
        media_type="text/event-stream",
        # no-transform: a proxy (the frontend's dev server, ngrok) mustn't compress the stream,
        # or it holds the words back and the reply arrives cut off
        headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"},
    )


def decoded(photo: str) -> bytes:
    """The photo's bytes; a `data:image/...;base64,` prefix is allowed."""
    try:
        return base64.b64decode(photo.split(",", 1)[-1], validate=True)
    except (binascii.Error, ValueError):
        raise UnreadablePhoto("The photo didn't arrive whole. Please send it again.") from None


class HistoryOut(BaseModel):
    conversation_id: str | None  # none: this browser hasn't chatted yet
    messages: list[dict[str, str]]  # {role: user | assistant | team, text}, oldest first


@router.get("/api/chat/{public_key}/history")
async def history(
    public_key: str, services: Svc, visitor_id: str = Query(min_length=8, max_length=64)
) -> HistoryOut:
    """The browser's last chat, so a reload shows it and carries on in it."""
    agent = await services.agents.by_key(public_key)  # 404 if unknown
    found = await services.conversations.last_web_chat(agent, visitor_id)
    if found is None:
        return HistoryOut(conversation_id=None, messages=[])
    return HistoryOut(conversation_id=found[0], messages=found[1])


class TeamOut(BaseModel):
    replies: list[str]  # oldest first; the page shows the ones it hasn't yet


@router.get("/api/chat/{public_key}/{conversation_id}/team")
async def team_replies(public_key: str, conversation_id: str, services: Svc) -> TeamOut:
    """What the team wrote from Telegram. The chat page asks every few seconds."""
    agent = await services.agents.by_key(public_key)  # 404 if unknown
    return TeamOut(replies=await services.conversations.team_replies(agent, conversation_id))
