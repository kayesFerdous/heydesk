"""Facebook Messenger (D60): the owner connects their Page, and Facebook posts its messages here.

One webhook for every Page, set once in our Meta app's dashboard; each post names its Page.
It answers at once and the turns run after, so Facebook never waits on the model.
"""

import json
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, Query, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from heydesk.api.deps import Svc, Workspace
from heydesk.errors import NotFound
from heydesk.models import Channel

router = APIRouter(tags=["messenger"])


class MessengerIn(BaseModel):
    token: str = Field(
        min_length=20, max_length=500, description="The Page's access token, from our Meta app"
    )


class MessengerOut(BaseModel):
    page_id: str
    link: str  # share it with customers: it opens a chat with the Page


def messenger_out(channel: Channel) -> MessengerOut:
    return MessengerOut(page_id=channel.address, link=f"https://m.me/{channel.address}")


@router.get("/api/agent/messenger")
async def get_messenger(ws: Workspace, services: Svc) -> MessengerOut:
    return messenger_out(await services.channels.messenger_of(ws))


@router.put("/api/agent/messenger")
async def connect_messenger(body: MessengerIn, ws: Workspace, services: Svc) -> MessengerOut:
    """Connects the business's Facebook Page: from now on the agent answers its messages."""
    return messenger_out(await services.channels.connect_messenger(ws, body.token.strip()))


@router.delete("/api/agent/messenger", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_messenger(ws: Workspace, services: Svc) -> None:
    await services.channels.disconnect_messenger(ws)


@router.get("/api/messenger", include_in_schema=False)
async def verify_messenger(
    services: Svc,
    mode: Annotated[str, Query(alias="hub.mode")] = "",
    token: Annotated[str, Query(alias="hub.verify_token")] = "",
    challenge: Annotated[str, Query(alias="hub.challenge")] = "",
) -> PlainTextResponse:
    """Facebook checks the webhook once, when it's set in the Meta app's dashboard."""
    if mode != "subscribe" or not services.channels.messenger_verify(token):
        raise NotFound("Not found")
    return PlainTextResponse(challenge)


@router.post("/api/messenger", include_in_schema=False)
async def messenger_webhook(
    request: Request,
    background: BackgroundTasks,
    services: Svc,
    signature: Annotated[str, Header(alias="X-Hub-Signature-256")] = "",
) -> dict[str, bool]:
    body = await request.body()
    if not services.channels.messenger_signed(body, signature):
        raise NotFound("Not found")
    for entry in json.loads(body).get("entry", []):
        channel = await services.channels.messenger_page(str(entry.get("id")))
        if channel is None:  # a Page that was disconnected
            continue
        for event in entry.get("messaging", []):
            background.add_task(services.channels.answer_messenger, channel, event)
    return {"ok": True}
