"""Telegram: the owner connects their bot, and Telegram posts its messages here.

The webhook answers at once and the turn runs after, so Telegram never waits on the model.
"""

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Body, Header, status
from pydantic import BaseModel, Field

from heydesk.api.deps import Svc, User, Workspace
from heydesk.models import Channel
from heydesk.services.team import owner_link

router = APIRouter(tags=["telegram"])


class TelegramIn(BaseModel):
    token: str = Field(
        min_length=20, max_length=200, description="The bot's token, from @BotFather's /newbot"
    )


class TelegramOut(BaseModel):
    username: str
    link: str  # share it with customers: it opens a chat with the bot
    # only for the owner: opening it sends them alerts there; None for a moderator, whose
    # own link is on /api/auth/me
    owner_link: str | None
    owner_linked: bool  # the owner has opened it


def telegram_out(channel: Channel) -> TelegramOut:
    return TelegramOut(
        username=channel.address,
        link=f"https://t.me/{channel.address}",
        owner_link=owner_link(channel),
        owner_linked=channel.owner_chat_id is not None,
    )


@router.get("/api/agent/telegram")
async def get_telegram(workspace_id: Workspace, services: Svc, current_user: User) -> TelegramOut:
    out = telegram_out(await services.channels.telegram_of(workspace_id))
    if current_user.role == "moderator":
        out.owner_link = None
    return out


@router.put("/api/agent/telegram")
async def connect_telegram(body: TelegramIn, workspace_id: Workspace, services: Svc) -> TelegramOut:
    """Connects the business's Telegram bot: from now on the agent answers its messages."""
    return telegram_out(await services.channels.connect_telegram(workspace_id, body.token.strip()))


@router.delete("/api/agent/telegram", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_telegram(workspace_id: Workspace, services: Svc) -> None:
    await services.channels.disconnect_telegram(workspace_id)


@router.post("/api/telegram/{channel_id}", include_in_schema=False)
async def telegram_webhook(
    channel_id: str,
    update: Annotated[dict[str, Any], Body()],
    background: BackgroundTasks,
    services: Svc,
    secret: Annotated[str, Header(alias="X-Telegram-Bot-Api-Secret-Token")] = "",
) -> dict[str, bool]:
    channel = await services.channels.telegram_webhook(channel_id, secret)  # 404 if not Telegram
    background.add_task(services.channels.answer_telegram, channel, update)
    return {"ok": True}
