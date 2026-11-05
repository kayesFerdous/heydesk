"""The team on Telegram: alerts for the owner, and the owner answering visitors.

The owner links their own chat with the business's bot once, through `owner_link`; a moderator
links theirs through `member_link`, and then a chat they have alerts them, not the owner.
Every alert ends with a `Ref:` line naming the conversation; whoever got it answers a visitor by
replying to it, and the agent stays quiet in that chat until they reply `/done`. Their words
reach a Telegram, Messenger or WhatsApp visitor from the business's bot, Page or number.
"""

import hashlib
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.models import Channel, Conversation, TeamEvent, Turn, User
from heydesk.ports.llm import LanguageModel, Message
from heydesk.ports.messenger import MessengerApi, MessengerUnavailable
from heydesk.ports.telegram import TelegramApi, TelegramUnavailable
from heydesk.ports.whatsapp import WhatsAppApi, WhatsAppUnavailable

log = logging.getLogger(__name__)

TELEGRAM, MESSENGER, WHATSAPP = "telegram", "messenger", "whatsapp"  # as `channels.kind`
HUMAN_STATUS = "human"  # the conversation's status while the team answers
TEAM_AUTHOR = "team"  # the "by" mark on a message the team wrote
REF_PATTERN = re.compile(r"Ref: ([0-9a-f]{32})")
MESSAGING_WINDOW = timedelta(
    hours=24
)  # Messenger and WhatsApp: a business writes first only inside it

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
CHAT_NOT_FOUND = "I can't find that chat any more."
NOT_A_CHAT = "That was a phone call, so I can't pass a message on. Call them back instead."
TAKEN_OVER = (
    "The agent stays quiet in this chat until you reply /done or ., or for 30 minutes after "
    "your last reply."
)
GIVEN_BACK = "The agent is answering this chat again."
TELEGRAM_REPLY = "Telegram reply"  # a team event's action: a reply to an alert


def owner_code(channel: Channel) -> str:
    """For the owner's link. Made from the webhook secret, so connecting again changes it."""
    return hashlib.sha256(f"owner:{channel.secret}".encode()).hexdigest()[:24]


def owner_link(channel: Channel) -> str:
    """Opens the bot and sends `/start owner_<code>`, which links the owner's chat."""
    return f"https://t.me/{channel.address}?start=owner_{owner_code(channel)}"


def member_code(channel: Channel, user_id: str) -> str:
    """For a moderator's link: theirs alone, and connecting the bot again changes it."""
    return hashlib.sha256(f"member:{channel.secret}:{user_id}".encode()).hexdigest()[:16]


def member_link(channel: Channel, user_id: str) -> str:
    """Opens the bot and sends `/start member_<user id><code>`, which links the member's chat."""
    return f"https://t.me/{channel.address}?start=member_{user_id}{member_code(channel, user_id)}"


def handoff_alert(messages: list[Message], title: str = "🙋 Someone wants a person") -> str:
    """The title, with the last few lines so whoever gets it knows what it's about."""
    said = [
        m
        for m in messages
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
    ]
    lines = [
        f"{'Customer' if m['role'] == 'user' else 'Agent'}: {m['content'][:300]}" for m in said[-4:]
    ]
    return f"{title}\n\n" + "\n".join(lines)


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

    async def alert(self, conversation: Conversation, text: str) -> None:
        """Sends an alert about `conversation` to the team member who has it, if they've linked their
        chat; otherwise to the owner, if they have."""
        channel = await self._channel(conversation.workspace_id)
        if channel is None:
            return
        async with self.sessions() as s:
            theirs = await s.scalar(
                select(User.telegram_chat_id)
                .join(Conversation, Conversation.assigned_to == User.id)
                .where(Conversation.id == conversation.id)
            )
        chat_id = theirs or channel.owner_chat_id
        if chat_id is None:
            return
        how = HOW_TO_ANSWER.get(conversation.channel, "")
        await self.send(channel, chat_id, f"{text}\n\n{how}\nRef: {conversation.id}")

    async def notify(self, workspace_id: str, text: str) -> None:
        """Tells the owner something about the business, not a chat (a sheet that can't be
        read), if they've linked their chat. No `Ref:`: there's no one to answer."""
        channel = await self._channel(workspace_id)
        if channel is not None and channel.owner_chat_id is not None:
            await self.send(channel, channel.owner_chat_id, text)

    async def send(
        self, channel: Channel, chat_id: str, text: str, choices: list[str] | None = None
    ) -> None:
        """One message from the business's bot, Page or number, with buttons for `choices`. A
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

    async def forward(self, conversation: Conversation, text: str) -> None:
        """A visitor wrote while the team is answering: pass it on, for the owner to reply to."""
        await self.alert(conversation, f"💬 Customer: {text}")

    async def tell_visitor(
        self, conversation_id: str, text: str, choices: list[str] | None = None
    ) -> None:
        """The business tells the visitor something the agent couldn't know (a payment came
        in). Added to the transcript like a team reply, so the web page shows it and the model
        sees it; sent on Telegram, Messenger or WhatsApp, with buttons for `choices` there.
        The conversation stays with the agent."""
        async with self.sessions() as s, s.begin():
            # locked: a turn saving at the same moment would drop this line (rule 5)
            conversation = await s.get_one(Conversation, conversation_id, with_for_update=True)
            said = {**self.llm.assistant_message(text), "by": TEAM_AUTHOR}
            conversation.messages = [
                *conversation.messages,
                said,
            ]  # a new list, so SQLAlchemy sees it
            conversation.updated_at = self.now()
        await self.send_to_visitor(conversation, text, choices)

    async def tell_visitor_if_allowed(
        self, conversation_id: str, text: str, choices: list[str] | None = None
    ) -> bool:
        """`tell_visitor` for a message the visitor didn't ask for: only inside the app's
        window, 24 hours since their last message on Messenger and WhatsApp (Meta refuses a
        message after it). Telegram and the web chat have none; a call can't take one. Says
        whether it was sent."""
        async with self.sessions() as s:
            conversation = await s.get_one(Conversation, conversation_id)
            last = await s.scalar(
                select(func.max(Turn.at)).where(Turn.conversation_id == conversation_id)
            )
        if conversation.channel == "voice":
            return False
        if conversation.channel in (MESSENGER, WHATSAPP):
            last = last and (last if last.tzinfo else last.replace(tzinfo=UTC))
            if last is None or self.now() - last > MESSAGING_WINDOW:
                return False
        await self.tell_visitor(conversation_id, text, choices)
        return True

    async def link_owner(self, channel: Channel, chat_id: str, code: str) -> None:
        if code != owner_code(channel):
            await self.send(channel, chat_id, OLD_LINK)
            return
        async with self.sessions() as s, s.begin():
            saved = await s.get_one(Channel, channel.id)
            saved.owner_chat_id = chat_id
        log.info("telegram owner linked", extra={"channel_id": channel.id})
        await self.send(channel, chat_id, LINKED)

    async def link_member(self, channel: Channel, chat_id: str, payload: str) -> None:
        """A moderator opened their link: alerts about their chats come to this chat."""
        user_id, code = payload[:32], payload[32:]
        async with self.sessions() as s, s.begin():
            user = await s.scalar(
                select(User).where(User.id == user_id, User.workspace_id == channel.workspace_id)
            )
            if user is None or code != member_code(channel, user_id):
                await self.send(channel, chat_id, OLD_LINK)
                return
            user.telegram_chat_id = chat_id
        log.info("telegram member linked", extra={"channel_id": channel.id})
        await self.send(channel, chat_id, LINKED)

    async def is_team(self, channel: Channel, chat_id: str) -> bool:
        """The owner's chat, or a moderator's linked one."""
        if chat_id == channel.owner_chat_id:
            return True
        async with self.sessions() as s:
            return await s.scalar(self._member(channel, chat_id)) is not None

    async def handle_team_reply(
        self, channel: Channel, chat_id: str, message: dict[str, Any]
    ) -> None:
        """Someone on the team replied to an alert: send their words to that visitor, or with
        `/done` give the chat back to the agent; "." takes it over, or gives it back.
        Their words go in the transcript as the agent's, and a reply is in the team's activity
        under their name."""
        owner = chat_id  # who answers: the owner, or a moderator
        text = (message.get("text") or "").strip()
        quoted = (message.get("reply_to_message") or {}).get("text") or ""
        found = REF_PATTERN.search(quoted)
        if not text or found is None:
            await self.send(channel, owner, PICK_A_CHAT)
            return
        to_visitor = False
        async with self.sessions() as s, s.begin():
            conversation = await s.scalar(
                select(Conversation)
                .where(
                    Conversation.id == found[1], Conversation.workspace_id == channel.workspace_id
                )
                .with_for_update()  # a turn saving at the same moment would drop this line
            )
            if conversation is None:
                await self.send(channel, owner, CHAT_NOT_FOUND)
                return
            if text == "/done" or (text == "." and conversation.status == HUMAN_STATUS):  # "."
                conversation.status, conversation.taken_over_at, answer = "active", None, GIVEN_BACK
            elif text == ".":  # taken over, with nothing said to them
                conversation.status, conversation.taken_over_at, answer = (
                    HUMAN_STATUS,
                    self.now(),
                    TAKEN_OVER,
                )
            elif conversation.channel == "voice":
                answer = NOT_A_CHAT
            else:
                said = {**self.llm.assistant_message(text), "by": TEAM_AUTHOR}
                conversation.messages = [
                    *conversation.messages,
                    said,
                ]  # a new list, so SQLAlchemy sees it
                answer = "" if conversation.status == HUMAN_STATUS else TAKEN_OVER
                conversation.status, to_visitor = HUMAN_STATUS, True
                conversation.taken_over_at = self.now()  # quiet for 30 minutes, it's given back
                who = await s.scalar(
                    self._member(channel, chat_id)
                    if chat_id != channel.owner_chat_id
                    else select(User).where(
                        User.workspace_id == channel.workspace_id, User.role == "owner"
                    )
                )
                if who is not None:
                    s.add(
                        TeamEvent(
                            workspace_id=channel.workspace_id,
                            actor_id=who.id,
                            actor_email=who.email,
                            action=TELEGRAM_REPLY,
                            target=f"conversation_id={conversation.id}",
                            at=self.now(),
                        )
                    )
            conversation.updated_at = self.now()
        if to_visitor:
            await self.send_to_visitor(conversation, text)
        if answer:
            await self.send(channel, owner, f"{answer}\nRef: {conversation.id}")
        log.info("team answered", extra={"conversation_id": conversation.id})

    async def send_to_visitor(
        self, conversation: Conversation, text: str, choices: list[str] | None = None
    ) -> None:
        """Sends the team's words to a messaging app's visitor; a web visitor's page fetches
        them (/team), and a call can't get them."""
        if conversation.channel not in (TELEGRAM, MESSENGER, WHATSAPP):
            return
        channel = await self._channel(conversation.workspace_id, conversation.channel)
        if channel is not None:
            await self.send(channel, str(conversation.contact), text, choices)

    async def member_alert_link(self, workspace_id: str, user_id: str) -> tuple[str | None, bool]:
        """A team member's own alerts link and whether they've opened it; no link without
        a Telegram bot."""
        channel = await self._channel(workspace_id)
        async with self.sessions() as s:
            user = await s.get_one(User, user_id)
        if channel is None:
            return None, False
        if user.role == "owner":  # the owner's alerts are the bot's own link
            return owner_link(channel), channel.owner_chat_id is not None
        return member_link(channel, user_id), user.telegram_chat_id is not None

    def _member(self, channel: Channel, chat_id: str) -> Any:
        return select(User).where(
            User.workspace_id == channel.workspace_id, User.telegram_chat_id == chat_id
        )

    async def _channel(self, workspace_id: str, kind: str = TELEGRAM) -> Channel | None:
        """Alerts always go to Telegram; a visitor's channel can be any app."""
        async with self.sessions() as s:
            return await s.scalar(
                select(Channel).where(Channel.workspace_id == workspace_id, Channel.kind == kind)
            )
