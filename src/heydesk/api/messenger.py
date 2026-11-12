"""Facebook Messenger: the owner connects their Page, and Facebook posts its messages here.

One webhook for every Page, set once in our Meta app's dashboard; each post names its Page.
It answers at once and the turns run after, so Facebook never waits on the model.
"""

from fastapi import APIRouter, BackgroundTasks, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from heydesk.api.deps import Svc, WorkspaceId
from heydesk.api.meta import MetaChallenge, MetaPost
from heydesk.models import Channel
from heydesk.services.team import MESSENGER

router = APIRouter(tags=["messenger"])


class MessengerIn(BaseModel):
    token: str = Field(
        min_length=20, max_length=500, description="The Page's access token, from our Meta app"
    )
    page_id: str = Field(pattern=r"^\d{1,25}$", description="The Page's id, digits only")


class FacebookLoginIn(BaseModel):
    user_token: str = Field(
        min_length=20, max_length=1000, description="The token Facebook's login gave the browser"
    )


class FacebookPageIn(FacebookLoginIn):
    page_id: str = Field(pattern=r"^\d{1,25}$", description="One of the login's Pages")


class FacebookLoginOut(BaseModel):
    app_id: str
    config_id: str  # Facebook Login for Business's configuration: the permissions


class FacebookPageOut(BaseModel):
    id: str
    name: str


class MessengerOut(BaseModel):
    page_id: str
    link: str  # share it with customers: it opens a chat with the Page


def messenger_out(channel: Channel) -> MessengerOut:
    return MessengerOut(page_id=channel.address, link=f"https://m.me/{channel.address}")


@router.get("/api/agent/messenger")
async def get_messenger(workspace_id: WorkspaceId, services: Svc) -> MessengerOut:
    return messenger_out(await services.channel_links.channel_of(workspace_id, "messenger"))


@router.put("/api/agent/messenger")
async def connect_messenger(
    body: MessengerIn, workspace_id: WorkspaceId, services: Svc
) -> MessengerOut:
    """Connects the business's Facebook Page (its id and access token): the agent answers its messages."""
    channel = await services.channel_links.connect_messenger(
        workspace_id, body.token.strip(), body.page_id
    )
    return messenger_out(channel)


@router.get("/api/agent/messenger/login")
async def facebook_login(workspace_id: WorkspaceId, services: Svc) -> FacebookLoginOut:
    """What the Connect with Facebook button needs. 400 if the server has no app id."""
    return FacebookLoginOut(**services.channel_links.facebook_login())


@router.post("/api/agent/messenger/pages")
async def facebook_pages(
    body: FacebookLoginIn, workspace_id: WorkspaceId, services: Svc
) -> list[FacebookPageOut]:
    """The Pages the owner's Facebook login manages, to pick one. Never their tokens."""
    pages = await services.channel_links.facebook_pages(body.user_token)
    return [FacebookPageOut(id=p["id"], name=p["name"]) for p in pages]


@router.post("/api/agent/messenger/facebook")
async def connect_facebook(
    body: FacebookPageIn, workspace_id: WorkspaceId, services: Svc
) -> MessengerOut:
    """Connects the chosen Page with its token from the login: no id or token to copy."""
    return messenger_out(
        await services.channel_links.connect_facebook(workspace_id, body.user_token, body.page_id)
    )


@router.delete("/api/agent/messenger", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_messenger(workspace_id: WorkspaceId, services: Svc) -> None:
    await services.channel_links.disconnect_messenger(workspace_id)


@router.get("/api/messenger", include_in_schema=False)
async def verify_messenger(challenge: MetaChallenge) -> PlainTextResponse:
    """Facebook checks the webhook once, when it's set in the Meta app's dashboard."""
    return PlainTextResponse(challenge)


@router.post("/api/messenger", include_in_schema=False)
async def messenger_webhook(
    posted: MetaPost, background: BackgroundTasks, services: Svc
) -> dict[str, bool]:
    for entry in posted.get("entry", []):
        channel = await services.channel_links.channel_at(MESSENGER, str(entry.get("id")))
        if channel is None:  # a Page that was disconnected
            continue
        for event in entry.get("messaging", []):
            background.add_task(services.channels.answer_messenger, channel, event)
    return {"ok": True}
