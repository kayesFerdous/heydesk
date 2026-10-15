"""Messaging apps: a business connects its own Telegram bot (D42) or Facebook Page (D60), and
the agent answers there.

The app posts each message to our webhook; the reply is the whole turn, sent as one message.
"""

import asyncio
import contextlib
import hashlib
import hmac
import logging
import secrets
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.db import new_id
from heydesk.errors import HeyDeskError, InvalidConfig, NotFound
from heydesk.models import Channel
from heydesk.ports.messenger import MessengerApi, MessengerUnavailable
from heydesk.ports.telegram import TelegramApi, TelegramUnavailable
from heydesk.services.agents import AgentService
from heydesk.services.conversations import ConversationService
from heydesk.services.team import MESSENGER, TELEGRAM, TeamService

log = logging.getLogger(__name__)

NOT_TEXT = "I can only read text messages for now. Please type your question."
FAILED = "Sorry, something went wrong. Please try again."


def first_photo(message: dict[str, Any]) -> str | None:
    """The first image's URL in a Messenger message. A sticker is an image too, so it's not."""
    for attachment in message.get("attachments") or []:
        payload = attachment.get("payload") or {}
        if attachment.get("type") == "image" and "sticker_id" not in payload:
            return payload.get("url")
    return None


class ChannelService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        agents: AgentService,
        conversations: ConversationService,
        team: TeamService,
        telegram: TelegramApi,
        messenger: MessengerApi,
        settings: Settings,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.conversations = conversations
        self.team = team
        self.telegram = telegram
        self.messenger = messenger
        self.settings = settings
        self.now = now

    async def connect_telegram(self, ws: str, token: str) -> Channel:
        """Checks the token with Telegram and points the bot's messages at us. Connecting
        again (a new token) replaces the old bot."""
        if not self.settings.PUBLIC_URL:
            raise HeyDeskError("The server has no PUBLIC_URL, so Telegram can't reach it.")
        agent = await self.agents.get(ws)  # 404 if the business has no agent yet
        try:
            username = await self.telegram.get_me(token)
        except TelegramUnavailable:
            raise InvalidConfig(
                {"token": "Telegram doesn't know this token. Copy it again from @BotFather."}
            ) from None
        async with self.sessions() as s:
            channel = await self._find(s, ws, TELEGRAM)
        channel_id = channel.id if channel else new_id()
        secret = secrets.token_urlsafe(32)
        url = f"{self.settings.PUBLIC_URL.rstrip('/')}/api/telegram/{channel_id}"
        await self.telegram.set_webhook(token, url, secret)  # first: if it fails, nothing saved
        channel = await self._save(ws, agent.id, TELEGRAM, channel_id, token, secret, username)
        log.info("telegram connected", extra={"channel_id": channel_id})
        return channel

    async def telegram_of(self, ws: str) -> Channel:
        return await self._of(ws, TELEGRAM, "This business hasn't connected a Telegram bot.")

    async def disconnect_telegram(self, ws: str) -> None:
        channel = await self.telegram_of(ws)
        # a token revoked in @BotFather: Telegram has stopped sending anyway
        with contextlib.suppress(TelegramUnavailable):
            await self.telegram.delete_webhook(channel.token)
        await self._delete(channel)

    async def connect_messenger(self, ws: str, token: str, page_id: str) -> Channel:
        """Subscribes our app to the Page's messages, which also checks that the token is for
        that Page. Connecting again (a new token, or another Page) replaces the old one."""
        if not self.settings.META_APP_SECRET:
            raise HeyDeskError("The server has no META_APP_SECRET, so Messenger can't reach it.")
        agent = await self.agents.get(ws)  # 404 if the business has no agent yet
        try:
            taken = await self.messenger_page(page_id)
            if taken is not None and taken.workspace_id != ws:
                raise InvalidConfig({"page_id": "This Page is connected to another business."})
            await self.messenger.subscribe(token, page_id)  # first: if it fails, nothing saved
        except MessengerUnavailable:
            raise InvalidConfig(
                {
                    "token": "Facebook doesn't accept this token for this Page. "
                    "Check the Page id, or make a new Page access token."
                }
            ) from None
        async with self.sessions() as s:
            channel = await self._find(s, ws, MESSENGER)
        channel_id = channel.id if channel else new_id()
        channel = await self._save(ws, agent.id, MESSENGER, channel_id, token, "", page_id)
        log.info("messenger connected", extra={"channel_id": channel_id})
        return channel

    async def messenger_of(self, ws: str) -> Channel:
        return await self._of(ws, MESSENGER, "This business hasn't connected a Facebook Page.")

    async def disconnect_messenger(self, ws: str) -> None:
        channel = await self.messenger_of(ws)
        with contextlib.suppress(MessengerUnavailable):  # a token that has expired
            await self.messenger.unsubscribe(channel.token, channel.address)
        await self._delete(channel)

    def messenger_verify(self, token: str) -> bool:
        """The verify token we set in the Meta app's dashboard. None set: no webhook."""
        expected = self.settings.MESSENGER_VERIFY_TOKEN
        return bool(expected) and secrets.compare_digest(expected, token)

    def messenger_signed(self, body: bytes, signature: str) -> bool:
        """Facebook signs each webhook post with our app secret. No secret: nothing is trusted."""
        secret = self.settings.META_APP_SECRET.encode()
        expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        return bool(secret) and hmac.compare_digest(expected, signature)

    async def messenger_page(self, page_id: str) -> Channel | None:
        """The business connected to a Page: one webhook serves every Page, so it's by the id."""
        async with self.sessions() as s:
            return await s.scalar(
                select(Channel).where(Channel.kind == MESSENGER, Channel.address == page_id)
            )

    async def telegram_webhook(self, channel_id: str, secret: str) -> Channel:
        """The channel a webhook call is for. 404 unless the secret is the one we gave Telegram."""
        async with self.sessions() as s:
            channel = await s.get(Channel, channel_id)
        if channel is None or not secrets.compare_digest(channel.secret, secret):
            raise NotFound("Not found")
        return channel

    async def answer_telegram(self, channel: Channel, update: dict[str, Any]) -> None:
        """Answers one message from Telegram. Runs after the webhook has said OK."""
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private":
            return  # groups and edits aren't a visitor talking to the desk
        await self._answer(channel, str(chat["id"]), message)

    async def answer_messenger(self, channel: Channel, event: dict[str, Any]) -> None:
        """Answers one message to a Facebook Page. Runs after the webhook has said OK."""
        message = event.get("message")
        if not message or message.get("is_echo"):  # the Page's own messages
            return
        await self._answer(channel, str(event["sender"]["id"]), message)

    async def _answer(self, channel: Channel, chat_id: str, message: dict[str, Any]) -> None:
        """Nothing here may raise: a failure is logged and the visitor gets a polite line."""
        text = message.get("text")
        photo_url = first_photo(message)  # Messenger's (D62); Telegram's photos aren't read
        try:
            if channel.kind == TELEGRAM and text and text.startswith("/start owner_"):  # D43
                await self.team.link_owner(channel, chat_id, text.removeprefix("/start owner_"))
                return
            if chat_id == channel.owner_chat_id:  # the owner answering a visitor
                await self.team.owner_wrote(channel, message)
                return
            agent = await self.agents.by_id(channel.agent_id)
            photo = None
            if text is None and photo_url and agent.desk.takes_photos:
                photo = await self.messenger.download(photo_url)  # its URL expires
            if text is None and photo is None:  # a sticker, a voice note, a file
                await self._send(channel, chat_id, NOT_TEXT)
            elif text is not None and text.split(maxsplit=1)[:1] == ["/start"]:
                await self.conversations.restart(agent, channel.kind, chat_id)
                await self._send(channel, chat_id, agent.greeting)
            else:
                typing = asyncio.create_task(self._keep_typing(channel, chat_id))
                try:
                    reply = await self.conversations.reply(
                        agent, channel.kind, chat_id, text or "", photo
                    )
                finally:
                    typing.cancel()
                if reply.photos:
                    await self._send_cards(channel, chat_id, reply.photos)
                if reply.text:
                    await self._send(channel, chat_id, reply.text)
                if reply.handoff:
                    phone = reply.handoff.phone or reply.handoff.email
                    await self._send(
                        channel, chat_id, reply.handoff.message + (f"\n{phone}" if phone else "")
                    )
        except HeyDeskError as error:  # too long, or a suspended business: it says why
            await self._send(channel, chat_id, error.message)
        except Exception:
            log.exception("answer failed", extra={"channel_id": channel.id})
            await self._send(channel, chat_id, FAILED)

    async def _send(self, channel: Channel, chat_id: str, text: str) -> None:
        await self.team.send(channel, chat_id, text)

    async def _send_cards(
        self, channel: Channel, chat_id: str, photos: list[dict[str, str]]
    ) -> None:
        """The products `show_photos` chose, as one carousel (D62). Facebook fetches each
        photo from us, so its URL is made whole. If it fails, the text still goes."""
        cards = [{**p, "url": self.settings.public_url + p["url"]} for p in photos]
        try:
            await self.messenger.send_cards(channel.token, chat_id, cards)
        except MessengerUnavailable as error:
            log.warning("cards not sent: %s", error, extra={"channel_id": channel.id})

    async def _keep_typing(self, channel: Channel, chat_id: str) -> None:
        """ "typing…" lasts five seconds on Telegram; a reply can take longer."""
        while True:
            try:
                if channel.kind == MESSENGER:
                    await self.messenger.send_typing(channel.token, chat_id)
                else:
                    await self.telegram.send_typing(channel.token, chat_id)
            except (TelegramUnavailable, MessengerUnavailable):
                return
            await asyncio.sleep(4)

    async def _save(
        self,
        ws: str,
        agent_id: str,
        kind: str,
        channel_id: str,
        token: str,
        secret: str,
        address: str,
    ) -> Channel:
        async with self.sessions() as s, s.begin():
            channel = await self._find(s, ws, kind)
            if channel is None:
                channel = Channel(
                    id=channel_id,
                    workspace_id=ws,
                    agent_id=agent_id,
                    kind=kind,
                    created_at=self.now(),
                )
                s.add(channel)
            channel.token, channel.secret, channel.address = token, secret, address
            channel.owner_chat_id = None  # a new bot: the owner links their chat with it again
        return channel

    async def _of(self, ws: str, kind: str, missing: str) -> Channel:
        async with self.sessions() as s:
            channel = await self._find(s, ws, kind)
        if channel is None:
            raise NotFound(missing)
        return channel

    async def _delete(self, channel: Channel) -> None:
        async with self.sessions() as s, s.begin():
            await s.delete(await s.get_one(Channel, channel.id))

    async def _find(self, s: AsyncSession, ws: str, kind: str) -> Channel | None:
        return await s.scalar(
            select(Channel).where(Channel.workspace_id == ws, Channel.kind == kind)
        )
