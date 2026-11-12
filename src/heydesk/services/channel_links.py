"""Linking a business's messaging apps: its Telegram bot, Facebook Page or WhatsApp number,
connected and disconnected by the owner, and which business a page or number belongs to.
Answering the messages that come in is `services/channels.py`."""

import contextlib
import logging
import secrets
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.db import new_id
from heydesk.errors import HeyDeskError, InvalidConfig, NotFound
from heydesk.models import Channel, User
from heydesk.ports.messenger import MessengerApi, MessengerUnavailable
from heydesk.ports.telegram import TelegramApi, TelegramUnavailable
from heydesk.ports.whatsapp import WhatsAppApi, WhatsAppUnavailable
from heydesk.services.agents import AgentService
from heydesk.services.team import MESSENGER, TELEGRAM, WHATSAPP, find_channel

log = logging.getLogger(__name__)

# What the owner reads when an app isn't connected yet
NOT_CONNECTED = {
    TELEGRAM: "This business hasn't connected a Telegram bot.",
    MESSENGER: "This business hasn't connected a Facebook Page.",
    WHATSAPP: "This business hasn't connected a WhatsApp number.",
}


class ChannelLinkService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        agents: AgentService,
        telegram: TelegramApi,
        messenger: MessengerApi,
        whatsapp: WhatsAppApi,
        settings: Settings,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.telegram = telegram
        self.messenger = messenger
        self.whatsapp = whatsapp
        self.settings = settings
        self.now = now

    async def channel_of(self, workspace_id: str, kind: str) -> Channel:
        """The business's link to one app. 404 when it isn't connected."""
        async with self.sessions() as s:
            channel = await find_channel(s, workspace_id, kind)
        if channel is None:
            raise NotFound(NOT_CONNECTED[kind])
        return channel

    async def connect_telegram(self, workspace_id: str, token: str) -> Channel:
        """Checks the token with Telegram and points the bot's messages at us. Connecting
        again (a new token) replaces the old bot."""
        if not self.settings.PUBLIC_URL:
            raise HeyDeskError("The server has no PUBLIC_URL, so Telegram can't reach it.")
        agent = await self.agents.get(workspace_id)  # 404 if the business has no agent yet
        try:
            username = await self.telegram.get_me(token)
        except TelegramUnavailable:
            raise InvalidConfig(
                {"token": "Telegram doesn't know this token. Copy it again from @BotFather."}
            ) from None
        async with self.sessions() as s:
            channel = await find_channel(s, workspace_id, TELEGRAM)
        channel_id = channel.id if channel else new_id()
        secret = secrets.token_urlsafe(32)
        url = f"{self.settings.PUBLIC_URL.rstrip('/')}/api/telegram/{channel_id}"
        await self.telegram.set_webhook(token, url, secret)  # first: if it fails, nothing saved
        channel = await self._save(
            workspace_id, agent.id, TELEGRAM, token, secret, username, channel_id
        )
        log.info("telegram connected", extra={"channel_id": channel_id})
        return channel

    async def refresh_telegram(self) -> None:
        """Points every bot's webhook at this server again, as the app starts. Telegram keeps
        the `allowed_updates` it was given when connected, so a bot connected before button
        taps never got them; and a tunnel's address changes on every run. Oldest first:
        a bot connected twice answers for the newest. Messages waiting for us are kept."""
        if not self.settings.PUBLIC_URL:
            return
        async with self.sessions() as s:
            found = await s.scalars(
                select(Channel).where(Channel.kind == TELEGRAM).order_by(Channel.created_at)
            )
            channels = list(found)
        for channel in channels:
            url = f"{self.settings.PUBLIC_URL.rstrip('/')}/api/telegram/{channel.id}"
            try:
                await self.telegram.set_webhook(channel.token, url, channel.secret, False)
            except TelegramUnavailable as error:  # a revoked token: the rest still go
                log.warning("webhook not set: %s", error, extra={"channel_id": channel.id})

    async def disconnect_telegram(self, workspace_id: str) -> None:
        channel = await self.channel_of(workspace_id, TELEGRAM)
        # a token revoked in @BotFather: Telegram has stopped sending anyway
        with contextlib.suppress(TelegramUnavailable):
            await self.telegram.delete_webhook(channel.token)
        await self._delete(channel)

    async def connect_messenger(self, workspace_id: str, token: str, page_id: str) -> Channel:
        """Subscribes our app to the Page's messages, which also checks that the token is for
        that Page. Connecting again (a new token, or another Page) replaces the old one."""
        if not self.settings.META_APP_SECRET:
            raise HeyDeskError("The server has no META_APP_SECRET, so Messenger can't reach it.")
        agent = await self.agents.get(workspace_id)  # 404 if the business has no agent yet
        await self._refuse_if_taken(MESSENGER, page_id, workspace_id, "page_id")
        try:
            await self.messenger.subscribe(token, page_id)  # first: if it fails, nothing saved
        except MessengerUnavailable:
            raise InvalidConfig(
                {
                    "token": "Facebook doesn't accept this token for this Page. "
                    "Check the Page id, or make a new Page access token."
                }
            ) from None
        channel = await self._save(workspace_id, agent.id, MESSENGER, token, "", page_id)
        log.info("messenger connected", extra={"channel_id": channel.id})
        return channel

    def facebook_login(self) -> dict[str, str]:
        """What the owner's Connect with Facebook button needs: our app's id, and the
        Facebook Login for Business configuration that holds the permissions it asks for."""
        s = self.settings
        if not (s.META_APP_ID and s.META_APP_SECRET and s.META_LOGIN_CONFIG_ID):
            raise HeyDeskError(
                "The server has no META_APP_ID or META_LOGIN_CONFIG_ID, so Facebook login is off."
            )
        return {"app_id": s.META_APP_ID, "config_id": s.META_LOGIN_CONFIG_ID}

    async def facebook_pages(self, user_token: str) -> list[dict[str, str]]:
        """The Pages the owner's Facebook login manages, with their tokens."""
        self.facebook_login()  # the app's id and secret are set
        try:
            return await self.messenger.pages(
                self.settings.META_APP_ID, self.settings.META_APP_SECRET, user_token
            )
        except MessengerUnavailable:
            raise InvalidConfig(
                {"user_token": "Facebook didn't accept this login. Try connecting again."}
            ) from None

    async def connect_facebook(self, workspace_id: str, user_token: str, page_id: str) -> Channel:
        """Connects one of the login's Pages with its own token, as if typed in."""
        pages = await self.facebook_pages(user_token)
        page = next((p for p in pages if p["id"] == page_id), None)
        if page is None:
            raise InvalidConfig({"page_id": "This Facebook login doesn't manage that Page."})
        return await self.connect_messenger(workspace_id, page["token"], page_id)

    async def disconnect_messenger(self, workspace_id: str) -> None:
        channel = await self.channel_of(workspace_id, MESSENGER)
        with contextlib.suppress(MessengerUnavailable):  # a token that has expired
            await self.messenger.unsubscribe(channel.token, channel.address)
        await self._delete(channel)

    async def connect_whatsapp(
        self, workspace_id: str, token: str, account_id: str, phone_id: str
    ) -> Channel:
        """Subscribes our app to the business account's messages, which also checks the token,
        the account and the number. Connecting again replaces the old number."""
        if not self.settings.META_APP_SECRET:
            raise HeyDeskError("The server has no META_APP_SECRET, so WhatsApp can't reach it.")
        agent = await self.agents.get(workspace_id)  # 404 if the business has no agent yet
        await self._refuse_if_taken(WHATSAPP, phone_id, workspace_id, "phone_id")
        try:
            # first: if it fails, nothing saved
            await self.whatsapp.subscribe(token, account_id, phone_id)
        except WhatsAppUnavailable:
            raise InvalidConfig(
                {
                    "token": "Meta doesn't accept this token for this account and number. "
                    "Check both ids, or make a new access token."
                }
            ) from None
        channel = await self._save(workspace_id, agent.id, WHATSAPP, token, "", phone_id)
        log.info("whatsapp connected", extra={"channel_id": channel.id})
        return channel

    def whatsapp_login(self) -> dict[str, str]:
        """What the owner's Connect with WhatsApp button needs: our app's id, and the
        Embedded Signup configuration."""
        s = self.settings
        if not (s.META_APP_ID and s.META_APP_SECRET and s.META_WA_CONFIG_ID):
            raise HeyDeskError(
                "The server has no META_APP_ID or META_WA_CONFIG_ID, so WhatsApp signup is off."
            )
        return {"app_id": s.META_APP_ID, "config_id": s.META_WA_CONFIG_ID}

    async def connect_whatsapp_signup(
        self, workspace_id: str, code: str, account_id: str, phone_id: str
    ) -> Channel:
        """Embedded Signup's code becomes the business's token, the number is registered on
        the Cloud API, then it's connected as if typed in."""
        self.whatsapp_login()  # the app's id, secret and configuration are set
        await self._refuse_if_taken(WHATSAPP, phone_id, workspace_id, "phone_id")
        try:
            token = await self.whatsapp.token_from_code(
                self.settings.META_APP_ID, self.settings.META_APP_SECRET, code
            )
        except WhatsAppUnavailable:
            raise InvalidConfig(
                {"code": "Meta didn't accept this signup. Try connecting again."}
            ) from None
        try:  # the PIN isn't kept: the owner can reset it in WhatsApp Manager
            await self.whatsapp.register(token, phone_id, f"{secrets.randbelow(10**6):06d}")
        except WhatsAppUnavailable:
            raise InvalidConfig(
                {"phone_id": "Meta couldn't put this number on WhatsApp's Cloud API. Try again."}
            ) from None
        return await self.connect_whatsapp(workspace_id, token, account_id, phone_id)

    async def disconnect_whatsapp(self, workspace_id: str) -> None:
        """Our app stays subscribed to the account (it may have other numbers); this number's
        messages find no channel and aren't answered."""
        await self._delete(await self.channel_of(workspace_id, WHATSAPP))

    async def channel_at(self, kind: str, address: str) -> Channel | None:
        """The channel a Page's id or a WhatsApp number's id belongs to, or None. One webhook
        serves every Page and every number, so a post is matched by that id."""
        async with self.sessions() as s:
            return await s.scalar(
                select(Channel).where(Channel.kind == kind, Channel.address == address)
            )

    async def _refuse_if_taken(
        self, kind: str, address: str, workspace_id: str, field: str
    ) -> None:
        """A Page or a number answers for one business only."""
        taken = await self.channel_at(kind, address)
        if taken is not None and taken.workspace_id != workspace_id:
            what = "Page" if kind == MESSENGER else "number"
            raise InvalidConfig({field: f"This {what} is connected to another business."})

    async def _save(
        self,
        workspace_id: str,
        agent_id: str,
        kind: str,
        token: str,
        secret: str,
        address: str,
        channel_id: str | None = None,
    ) -> Channel:
        """Saves the business's link to an app, over the one it had. Telegram passes the new
        row's id: its webhook's address names it, and is set before anything is saved."""
        async with self.sessions() as s, s.begin():
            channel = await find_channel(s, workspace_id, kind)
            if channel is None:
                channel = Channel(
                    id=channel_id or new_id(),
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    kind=kind,
                    created_at=self.now(),
                )
                s.add(channel)
            channel.token, channel.secret, channel.address = token, secret, address
            channel.owner_chat_id = None  # a new bot: the owner links their chat with it again
            if kind == TELEGRAM:  # and so does each moderator
                await s.execute(
                    update(User)
                    .where(User.workspace_id == workspace_id)
                    .values(telegram_chat_id=None)
                )
        return channel

    async def _delete(self, channel: Channel) -> None:
        async with self.sessions() as s, s.begin():
            await s.delete(await s.get_one(Channel, channel.id))
