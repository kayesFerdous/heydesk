"""WhatsApp: the owner connects their number, and Meta posts its messages here.

One webhook for every number, set once in our Meta app's dashboard; each message names its
number's id. Signed and checked like Messenger's. It answers at once and the turns run after.
"""

from fastapi import APIRouter, BackgroundTasks, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from heydesk.api.deps import Svc, WorkspaceId
from heydesk.api.meta import MetaChallenge, MetaPost
from heydesk.services.team import WHATSAPP

router = APIRouter(tags=["whatsapp"])

Digits = Field(pattern=r"^\d{1,25}$")


class WhatsAppIn(BaseModel):
    token: str = Field(
        min_length=20, max_length=500, description="A system user's access token, from our Meta app"
    )
    account_id: str = Digits  # the WhatsApp Business Account's id
    phone_id: str = Digits  # the number's id (not the number itself)


class WhatsAppSignupIn(BaseModel):
    code: str = Field(
        min_length=10, max_length=1000, description="The code Embedded Signup gave the browser"
    )
    account_id: str = Digits  # from the popup's FINISH message: waba_id
    phone_id: str = Digits  # and phone_number_id


class WhatsAppLoginOut(BaseModel):
    app_id: str
    config_id: str  # the WhatsApp Embedded Signup configuration


class WhatsAppOut(BaseModel):
    phone_id: str


@router.get("/api/agent/whatsapp")
async def get_whatsapp(workspace_id: WorkspaceId, services: Svc) -> WhatsAppOut:
    return WhatsAppOut(
        phone_id=(await services.channel_links.channel_of(workspace_id, "whatsapp")).address
    )


@router.put("/api/agent/whatsapp")
async def connect_whatsapp(
    body: WhatsAppIn, workspace_id: WorkspaceId, services: Svc
) -> WhatsAppOut:
    """Connects the business's WhatsApp number: the agent answers its messages."""
    channel = await services.channel_links.connect_whatsapp(
        workspace_id, body.token.strip(), body.account_id, body.phone_id
    )
    return WhatsAppOut(phone_id=channel.address)


@router.get("/api/agent/whatsapp/login")
async def whatsapp_login(workspace_id: WorkspaceId, services: Svc) -> WhatsAppLoginOut:
    """What the Connect with WhatsApp button needs. 400 if the server has no app id."""
    return WhatsAppLoginOut(**services.channel_links.whatsapp_login())


@router.post("/api/agent/whatsapp/signup")
async def whatsapp_signup(
    body: WhatsAppSignupIn, workspace_id: WorkspaceId, services: Svc
) -> WhatsAppOut:
    """Connects the number the owner added in Meta's Embedded Signup window: nothing to copy."""
    channel = await services.channel_links.connect_whatsapp_signup(
        workspace_id, body.code, body.account_id, body.phone_id
    )
    return WhatsAppOut(phone_id=channel.address)


@router.delete("/api/agent/whatsapp", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_whatsapp(workspace_id: WorkspaceId, services: Svc) -> None:
    await services.channel_links.disconnect_whatsapp(workspace_id)


@router.get("/api/whatsapp", include_in_schema=False)
async def verify_whatsapp(challenge: MetaChallenge) -> PlainTextResponse:
    """Meta checks the webhook once, when it's set in the Meta app's dashboard."""
    return PlainTextResponse(challenge)


@router.post("/api/whatsapp", include_in_schema=False)
async def whatsapp_webhook(
    posted: MetaPost, background: BackgroundTasks, services: Svc
) -> dict[str, bool]:
    for entry in posted.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value") or {}  # delivery and read statuses have no messages
            phone_id = str((value.get("metadata") or {}).get("phone_number_id"))
            channel = await services.channel_links.channel_at(WHATSAPP, phone_id)
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
