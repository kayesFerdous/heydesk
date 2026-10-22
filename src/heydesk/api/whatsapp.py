"""WhatsApp (D103): the owner connects their number, and Meta posts its messages here.

One webhook for every number, set once in our Meta app's dashboard; each message names its
number's id. Signed and checked like Messenger's. It answers at once and the turns run after.
"""

import json
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, Query, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from heydesk.api.deps import Svc, Workspace
from heydesk.errors import NotFound

router = APIRouter(tags=["whatsapp"])

Digits = Field(pattern=r"^\d{1,25}$")


class WhatsAppIn(BaseModel):
    token: str = Field(
        min_length=20, max_length=500, description="A system user's access token, from our Meta app"
    )
    account_id: str = Digits  # the WhatsApp Business Account's id
    phone_id: str = Digits  # the number's id (not the number itself)


class WhatsAppOut(BaseModel):
    phone_id: str


@router.get("/api/agent/whatsapp")
async def get_whatsapp(ws: Workspace, services: Svc) -> WhatsAppOut:
    return WhatsAppOut(phone_id=(await services.channels.whatsapp_of(ws)).address)


@router.put("/api/agent/whatsapp")
async def connect_whatsapp(body: WhatsAppIn, ws: Workspace, services: Svc) -> WhatsAppOut:
    """Connects the business's WhatsApp number: the agent answers its messages."""
    channel = await services.channels.connect_whatsapp(
        ws, body.token.strip(), body.account_id, body.phone_id
    )
    return WhatsAppOut(phone_id=channel.address)


@router.delete("/api/agent/whatsapp", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_whatsapp(ws: Workspace, services: Svc) -> None:
    await services.channels.disconnect_whatsapp(ws)


@router.get("/api/whatsapp", include_in_schema=False)
async def verify_whatsapp(
    services: Svc,
    mode: Annotated[str, Query(alias="hub.mode")] = "",
    token: Annotated[str, Query(alias="hub.verify_token")] = "",
    challenge: Annotated[str, Query(alias="hub.challenge")] = "",
) -> PlainTextResponse:
    """Meta checks the webhook once, when it's set in the Meta app's dashboard."""
    if mode != "subscribe" or not services.channels.messenger_verify(token):
        raise NotFound("Not found")
    return PlainTextResponse(challenge)


@router.post("/api/whatsapp", include_in_schema=False)
async def whatsapp_webhook(
    request: Request,
    background: BackgroundTasks,
    services: Svc,
    signature: Annotated[str, Header(alias="X-Hub-Signature-256")] = "",
) -> dict[str, bool]:
    body = await request.body()
    if not services.channels.messenger_signed(body, signature):
        raise NotFound("Not found")
    for entry in json.loads(body).get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value") or {}  # delivery and read statuses have no messages
            phone_id = str((value.get("metadata") or {}).get("phone_number_id"))
            channel = await services.channels.whatsapp_number(phone_id)
            if channel is None:  # a number that was disconnected
                continue
            names = {
                c.get("wa_id"): (c.get("profile") or {}).get("name")
                for c in value.get("contacts", [])
            }
            for message in value.get("messages", []):
                name = names.get(message.get("from"))
                background.add_task(services.channels.answer_whatsapp, channel, message, name)
    return {"ok": True}
