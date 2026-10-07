"""Messaging apps: a business connects its own Telegram bot, and the agent answers there (D42).

Telegram posts each message to our webhook; the reply is the whole turn, sent as one message.
"""

import asyncio
import contextlib
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
from heydesk.ports.telegram import TelegramApi, TelegramUnavailable
from heydesk.services.agents import AgentService
from heydesk.services.conversations import ConversationService

log = logging.getLogger(__name__)

TELEGRAM = "telegram"
NOT_TEXT = "I can only read text messages for now. Please type your question."
FAILED = "Sorry, something went wrong. Please try again."


class ChannelService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        agents: AgentService,
        conversations: ConversationService,
        telegram: TelegramApi,
        settings: Settings,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.conversations = conversations
        self.telegram = telegram
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
            channel = await self._find(s, ws)
        channel_id = channel.id if channel else new_id()
        secret = secrets.token_urlsafe(32)
        url = f"{self.settings.PUBLIC_URL.rstrip('/')}/api/telegram/{channel_id}"
        await self.telegram.set_webhook(token, url, secret)  # first: if it fails, nothing saved
        async with self.sessions() as s, s.begin():
            channel = await self._find(s, ws)
            if channel is None:
                channel = Channel(
                    id=channel_id,
                    workspace_id=ws,
                    agent_id=agent.id,
                    kind=TELEGRAM,
                    created_at=self.now(),
                )
                s.add(channel)
            channel.token, channel.secret, channel.address = token, secret, username
        log.info("telegram connected", extra={"channel_id": channel_id})
        return channel

    async def telegram_of(self, ws: str) -> Channel:
        async with self.sessions() as s:
            channel = await self._find(s, ws)
        if channel is None:
            raise NotFound("This business hasn't connected a Telegram bot.")
        return channel

    async def disconnect_telegram(self, ws: str) -> None:
        channel = await self.telegram_of(ws)
        # a token revoked in @BotFather: Telegram has stopped sending anyway
        with contextlib.suppress(TelegramUnavailable):
            await self.telegram.delete_webhook(channel.token)
        async with self.sessions() as s, s.begin():
            await s.delete(await s.get_one(Channel, channel.id))

    async def telegram_webhook(self, channel_id: str, secret: str) -> Channel:
        """The channel a webhook call is for. 404 unless the secret is the one we gave Telegram."""
        async with self.sessions() as s:
            channel = await s.get(Channel, channel_id)
        if channel is None or not secrets.compare_digest(channel.secret, secret):
            raise NotFound("Not found")
        return channel

    async def answer_telegram(self, channel: Channel, update: dict[str, Any]) -> None:
        """Answers one message from Telegram. Runs after the webhook has said OK, so nothing
        here may raise: a failure is logged and the visitor gets a polite line."""
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private":
            return  # groups and edits aren't a visitor talking to the desk
        chat_id, text = str(chat["id"]), message.get("text")
        try:
            agent = await self.agents.by_id(channel.agent_id)
            if text is None:  # a photo, a sticker, a voice note
                await self._send(channel, chat_id, NOT_TEXT)
            elif text.split(maxsplit=1)[:1] == ["/start"]:
                await self.conversations.restart(agent, TELEGRAM, chat_id)
                await self._send(channel, chat_id, agent.greeting)
            else:
                typing = asyncio.create_task(self._keep_typing(channel, chat_id))
                try:
                    reply = await self.conversations.reply(agent, TELEGRAM, chat_id, text)
                finally:
                    typing.cancel()
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
            log.exception("telegram answer failed", extra={"channel_id": channel.id})
            await self._send(channel, chat_id, FAILED)

    async def _send(self, channel: Channel, chat_id: str, text: str) -> None:
        try:
            await self.telegram.send_message(channel.token, chat_id, text)
        except TelegramUnavailable as error:
            log.warning("telegram send failed: %s", error, extra={"channel_id": channel.id})

    async def _keep_typing(self, channel: Channel, chat_id: str) -> None:
        """ "typing…" lasts five seconds on Telegram; a reply can take longer."""
        while True:
            try:
                await self.telegram.send_typing(channel.token, chat_id)
            except TelegramUnavailable:
                return
            await asyncio.sleep(4)

    async def _find(self, s: AsyncSession, ws: str) -> Channel | None:
        return await s.scalar(
            select(Channel).where(Channel.workspace_id == ws, Channel.kind == TELEGRAM)
        )
