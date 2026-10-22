"""The team on Telegram (D43): alerts for the owner, and the owner answering visitors.

The owner links their own chat with the business's bot once, through `owner_link`. Every
alert ends with a `Ref:` line naming the conversation; the owner answers a visitor by
replying to it, and the agent stays quiet in that chat until they reply `/done`. Their words
reach a Telegram, Messenger or WhatsApp visitor from the business's bot, Page or number (D60,
D103).
"""

import hashlib
import logging
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.models import Channel, Conversation
from heydesk.ports.llm import LanguageModel, Message
from heydesk.ports.messenger import MessengerApi, MessengerUnavailable
from heydesk.ports.telegram import TelegramApi, TelegramUnavailable
from heydesk.ports.whatsapp import WhatsAppApi, WhatsAppUnavailable

log = logging.getLogger(__name__)

TELEGRAM, MESSENGER, WHATSAPP = "telegram", "messenger", "whatsapp"  # as `channels.kind`
HUMAN = "human"  # the conversation's status while the team answers
TEAM = "team"  # the "by" mark on a message the team wrote
REF = re.compile(r"Ref: ([0-9a-f]{32})")

HOW_TO_ANSWER = {
    "telegram": "Reply to this message to answer them.",
    "web": "Reply to this message to answer them in the web chat.",
    "messenger": "Reply to this message to answer them on Messenger, within 24 hours of their "
    "last message.",
    "whatsapp": "Reply to this message to answer them on WhatsApp, within 24 hours of their "
    "last message.",
    "voice": "It was a phone call: call them back if they left a number.",
}
LINKED = "Done! You'll get alerts here. Reply to one to answer that customer yourself."
OLD_LINK = "That link is out of date. Open the newest one from your HeyDesk page."
PICK_A_CHAT = "To answer a customer, reply to an alert or to one of their messages."
GONE = "I can't find that chat any more."
NOT_A_CHAT = "That was a phone call, so I can't pass a message on. Call them back instead."
TAKEN_OVER = "Sent. The agent stays quiet in this chat until you reply /done."
GIVEN_BACK = "The agent is answering this chat again."


def owner_code(channel: Channel) -> str:
    """For the owner's link. Made from the webhook secret, so connecting again changes it."""
    return hashlib.sha256(f"owner:{channel.secret}".encode()).hexdigest()[:24]


def owner_link(channel: Channel) -> str:
    """Opens the bot and sends `/start owner_<code>`, which links the owner's chat."""
    return f"https://t.me/{channel.address}?start=owner_{owner_code(channel)}"


def handoff_alert(messages: list[Message]) -> str:
    """ "Someone wants a person", with the last few lines so the owner knows what it's about."""
    said = [
        m
        for m in messages
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
    ]
    lines = [
        f"{'Customer' if m['role'] == 'user' else 'Agent'}: {m['content'][:300]}" for m in said[-4:]
    ]
    return "🙋 Someone wants a person\n\n" + "\n".join(lines)


class TeamService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        llm: LanguageModel,  # only for the shape of a stored message
        telegram: TelegramApi,
        messenger: MessengerApi,
        whatsapp: WhatsAppApi,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.llm = llm
        self.telegram = telegram
        self.messenger = messenger
        self.whatsapp = whatsapp
        self.now = now

    async def alert(self, conv: Conversation, text: str) -> None:
        """Sends the owner an alert about `conv`, if they've linked their chat."""
        channel = await self._channel(conv.workspace_id)
        if channel is None or channel.owner_chat_id is None:
            return
        how = HOW_TO_ANSWER.get(conv.channel, "")
        await self.send(channel, channel.owner_chat_id, f"{text}\n\n{how}\nRef: {conv.id}")

    async def notify(self, ws: str, text: str) -> None:
        """Tells the owner something about the business, not a chat (a sheet that can't be
        read, D58), if they've linked their chat. No `Ref:`: there's no one to answer."""
        channel = await self._channel(ws)
        if channel is not None and channel.owner_chat_id is not None:
            await self.send(channel, channel.owner_chat_id, text)

    async def send(
        self, channel: Channel, chat_id: str, text: str, choices: list[str] | None = None
    ) -> None:
        """One message from the business's bot, Page or number, with buttons for `choices` (D99). A
        failure is logged, never raised."""
        try:
            if channel.kind == MESSENGER:
                await self.messenger.send_message(channel.token, chat_id, text, choices)
            elif channel.kind == WHATSAPP:
                await self.whatsapp.send_message(
                    channel.token, channel.address, chat_id, text, choices
                )
            else:
                await self.telegram.send_message(channel.token, chat_id, text, choices)
        except (TelegramUnavailable, MessengerUnavailable, WhatsAppUnavailable) as error:
            log.warning("send failed: %s", error, extra={"channel_id": channel.id})

    async def forward(self, conv: Conversation, text: str) -> None:
        """A visitor wrote while the team is answering: pass it on, for the owner to reply to."""
        await self.alert(conv, f"💬 Customer: {text}")

    async def tell_visitor(self, conversation_id: str, text: str) -> None:
        """The business tells the visitor something the agent couldn't know (a payment came
        in). Added to the transcript like a team reply, so the web page shows it and the model
        sees it; sent on Telegram, Messenger or WhatsApp. The conversation stays with the agent."""
        async with self.sessions() as s, s.begin():
            conv = await s.get_one(Conversation, conversation_id)
            said = {**self.llm.assistant_message(text), "by": TEAM}
            conv.messages = [*conv.messages, said]  # a new list, so SQLAlchemy sees it
            conv.updated_at = self.now()
        await self._to_visitor(conv, text)

    async def link_owner(self, channel: Channel, chat_id: str, code: str) -> None:
        if code != owner_code(channel):
            await self.send(channel, chat_id, OLD_LINK)
            return
        async with self.sessions() as s, s.begin():
            saved = await s.get_one(Channel, channel.id)
            saved.owner_chat_id = chat_id
        log.info("telegram owner linked", extra={"channel_id": channel.id})
        await self.send(channel, chat_id, LINKED)

    async def owner_wrote(self, channel: Channel, message: dict[str, Any]) -> None:
        """The owner replied to an alert: send their words to that visitor, or with `/done`
        give the chat back to the agent. Their words go in the transcript as the agent's."""
        owner = str(channel.owner_chat_id)
        text = (message.get("text") or "").strip()
        quoted = (message.get("reply_to_message") or {}).get("text") or ""
        found = REF.search(quoted)
        if not text or found is None:
            await self.send(channel, owner, PICK_A_CHAT)
            return
        to_visitor = False
        async with self.sessions() as s, s.begin():
            conv = await s.scalar(
                select(Conversation).where(
                    Conversation.id == found[1], Conversation.workspace_id == channel.workspace_id
                )
            )
            if conv is None:
                await self.send(channel, owner, GONE)
                return
            if text == "/done":
                conv.status, answer = "active", GIVEN_BACK
            elif conv.channel == "voice":
                answer = NOT_A_CHAT
            else:
                said = {**self.llm.assistant_message(text), "by": TEAM}
                conv.messages = [*conv.messages, said]  # a new list, so SQLAlchemy sees it
                answer = "" if conv.status == HUMAN else TAKEN_OVER
                conv.status, to_visitor = HUMAN, True
            conv.updated_at = self.now()
        if to_visitor:
            await self._to_visitor(conv, text)
        if answer:
            await self.send(channel, owner, f"{answer}\nRef: {conv.id}")
        log.info("team answered", extra={"conversation_id": conv.id})

    async def _to_visitor(self, conv: Conversation, text: str) -> None:
        """Sends the team's words to a messaging app's visitor; a web visitor's page fetches
        them (/team), and a call can't get them."""
        if conv.channel not in (TELEGRAM, MESSENGER, WHATSAPP):
            return
        channel = await self._channel(conv.workspace_id, conv.channel)
        if channel is not None:
            await self.send(channel, str(conv.contact), text)

    async def _channel(self, ws: str, kind: str = TELEGRAM) -> Channel | None:
        """Alerts always go to Telegram; a visitor's channel can be any app."""
        async with self.sessions() as s:
            return await s.scalar(
                select(Channel).where(Channel.workspace_id == ws, Channel.kind == kind)
            )
