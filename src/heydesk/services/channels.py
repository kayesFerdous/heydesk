"""Messaging apps: a business connects its own Telegram bot (D42), Facebook Page (D60) or
WhatsApp number (D103), and the agent answers there.

The app posts each message to our webhook. The agent waits a moment for the visitor's next
message, then answers all of them in one turn, sent as one message (D117).
"""

import asyncio
import contextlib
import hashlib
import hmac
import html
import logging
import secrets
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.db import new_id
from heydesk.desks.shop.values import ShopConfig, post_product
from heydesk.errors import HeyDeskError, InvalidConfig, NotFound, VisitorSilenced
from heydesk.models import Channel, PostComment
from heydesk.ports.media import MediaUnavailable
from heydesk.ports.messenger import OURS, MessengerApi, MessengerUnavailable
from heydesk.ports.telegram import TelegramApi, TelegramUnavailable
from heydesk.ports.whatsapp import WhatsAppApi, WhatsAppUnavailable
from heydesk.runtime.events import Card
from heydesk.runtime.speech import money
from heydesk.services.agents import AgentService
from heydesk.services.conversations import ConversationService
from heydesk.services.photos import PhotoService
from heydesk.services.team import MESSENGER, TELEGRAM, WHATSAPP, TeamService
from heydesk.services.visitors import VisitorService

log = logging.getLogger(__name__)

NOT_TEXT = "I can only read text messages for now. Please type your question."
FAILED = "Sorry, something went wrong. Please try again."
# Under a comment, for everyone (D141); the details go privately
IN_INBOX = "Sent you the details in Messenger 📩"
PHOTOS_SENT = 3  # on Telegram and WhatsApp a photo is a message; more cards are one list (D125)


def first_photo(message: dict[str, Any]) -> str | None:
    """The first image's URL in a Messenger message. A sticker is an image too, so it's not."""
    for attachment in message.get("attachments") or []:
        payload = attachment.get("payload") or {}
        if attachment.get("type") == "image" and "sticker_id" not in payload:
            return payload.get("url")
    return None


def buttons(choices: list[str]) -> list[str]:
    """The choices a button can carry (D99): Messenger takes 13 quick replies of 20 characters,
    and Telegram's button data is 64 bytes. A longer one is left out, so it's typed instead."""
    return [c for c in choices if len(c) <= 20 and len(c.encode()) <= 64][:13]


def card_text(card: Card, channel: str) -> str:
    """A card as a message's words (D111), a line each, its title in bold: Telegram's HTML
    (so every value is escaped), WhatsApp's *stars*; Messenger has no bold."""

    def esc(s: str) -> str:  # a name or an address could hold a "<"
        return html.escape(s, quote=False) if channel == TELEGRAM else s

    def bold(s: str) -> str:
        if channel == TELEGRAM:
            return f"<b>{s}</b>"
        return f"*{s}*" if channel == WHATSAPP else s

    data = card.data
    said: list[str] = []
    if card.kind == "tracking":
        steps = data["steps"]
        marks = [step_mark(s, i == len(steps) - 1 and len(steps) > 1) for i, s in enumerate(steps)]
        said.append(bold(esc(f"Order {data['number']}")))
        said.append(" → ".join(f"{m} {esc(s['label'])}" for m, s in zip(marks, steps, strict=True)))
        if data["note"]:
            said.append(esc(data["note"]))
        return "\n".join(said)
    if card.kind == "doctors":
        for d in data["doctors"]:
            said.append(f"{bold(esc(d['name']))} — {esc(d['speciality'])}")
            said.append(esc(f"{d['days']} · {d['hours']} · {d['fee']['text']}") + "\n")
        return "\n".join(said).strip()
    if card.kind == "receipt":
        said.append(bold(esc(f"{data['title']} · {data['number']}")))
        said.append(esc(f"Status: {data['status']}"))
    else:  # a summary
        said.append(bold(esc(data["title"])))
        for line in data["lines"]:
            label = f"{line['qty']} × {line['label']}" if "qty" in line else line["label"]
            cost = f" — {line['amount']['text']}" if "amount" in line else ""
            said.append(esc(label + cost))
    said += [esc(f"{r['label']}: {r['value']}") for r in data["rows"]]
    if "total" in data:
        said.append(bold(esc(f"Total: {data['total']['text']}")))
    if "order_url" in data:  # the apps make a link of it (D130)
        said.append(esc(f"Follow your order: {data['order_url']}"))
    return "\n".join(said)


def step_mark(step: dict[str, Any], last: bool) -> str:
    """✅ done, 🔵 where it is now, ⚪ still to come; the last step reached is done."""
    if step["current"] and not last:
        return "🔵"
    return "✅" if step["done"] else "⚪"


def about(doctor: dict[str, Any]) -> str:
    """A doctor's line under their name: "Medicine · Sun, Tue · 5:00 pm–9:00 pm · ৳800"."""
    return (
        f"{doctor['speciality']} · {doctor['days']} · {doctor['hours']} · {doctor['fee']['text']}"
    )


class ChannelService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        agents: AgentService,
        conversations: ConversationService,
        visitors: VisitorService,
        photos: PhotoService,
        team: TeamService,
        telegram: TelegramApi,
        messenger: MessengerApi,
        whatsapp: WhatsAppApi,
        settings: Settings,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.conversations = conversations
        self.visitors = visitors
        self.photos = photos
        self.team = team
        self.telegram = telegram
        self.messenger = messenger
        self.whatsapp = whatsapp
        self.settings = settings
        self.now = now
        # a chat's messages not answered yet, while it waits for more (D117); in memory, so
        # one server process
        self.waiting: dict[str, list[dict[str, Any]]] = {}
        # WhatsApp's webhook quotes only a message's id, so the buyers' recent messages are
        # kept here, by id, for their replies (D117)
        self.whatsapp_said: dict[str, dict[str, Any]] = {}

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

    def facebook_login(self) -> dict[str, str]:
        """What the owner's Connect with Facebook button needs (D118): our app's id, and the
        Facebook Login for Business configuration that holds the permissions it asks for (D119)."""
        s = self.settings
        if not (s.META_APP_ID and s.META_APP_SECRET and s.META_LOGIN_CONFIG_ID):
            raise HeyDeskError(
                "The server has no META_APP_ID or META_LOGIN_CONFIG_ID, so Facebook login is off."
            )
        return {"app_id": s.META_APP_ID, "config_id": s.META_LOGIN_CONFIG_ID}

    async def facebook_pages(self, user_token: str) -> list[dict[str, str]]:
        """The Pages the owner's Facebook login manages, with their tokens (D118)."""
        self.facebook_login()  # the app's id and secret are set
        try:
            return await self.messenger.pages(
                self.settings.META_APP_ID, self.settings.META_APP_SECRET, user_token
            )
        except MessengerUnavailable:
            raise InvalidConfig(
                {"user_token": "Facebook didn't accept this login. Try connecting again."}
            ) from None

    async def connect_facebook(self, ws: str, user_token: str, page_id: str) -> Channel:
        """Connects one of the login's Pages with its own token, as if typed in (D118)."""
        pages = await self.facebook_pages(user_token)
        page = next((p for p in pages if p["id"] == page_id), None)
        if page is None:
            raise InvalidConfig({"page_id": "This Facebook login doesn't manage that Page."})
        return await self.connect_messenger(ws, page["token"], page_id)

    async def messenger_of(self, ws: str) -> Channel:
        return await self._of(ws, MESSENGER, "This business hasn't connected a Facebook Page.")

    async def disconnect_messenger(self, ws: str) -> None:
        channel = await self.messenger_of(ws)
        with contextlib.suppress(MessengerUnavailable):  # a token that has expired
            await self.messenger.unsubscribe(channel.token, channel.address)
        await self._delete(channel)

    async def connect_whatsapp(
        self, ws: str, token: str, account_id: str, phone_id: str
    ) -> Channel:
        """Subscribes our app to the business account's messages, which also checks the token,
        the account and the number. Connecting again replaces the old number (D103)."""
        if not self.settings.META_APP_SECRET:
            raise HeyDeskError("The server has no META_APP_SECRET, so WhatsApp can't reach it.")
        agent = await self.agents.get(ws)  # 404 if the business has no agent yet
        taken = await self.whatsapp_number(phone_id)
        if taken is not None and taken.workspace_id != ws:
            raise InvalidConfig({"phone_id": "This number is connected to another business."})
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
        async with self.sessions() as s:
            channel = await self._find(s, ws, WHATSAPP)
        channel_id = channel.id if channel else new_id()
        channel = await self._save(ws, agent.id, WHATSAPP, channel_id, token, "", phone_id)
        log.info("whatsapp connected", extra={"channel_id": channel_id})
        return channel

    async def whatsapp_of(self, ws: str) -> Channel:
        return await self._of(ws, WHATSAPP, "This business hasn't connected a WhatsApp number.")

    async def disconnect_whatsapp(self, ws: str) -> None:
        """Our app stays subscribed to the account (it may have other numbers); this number's
        messages find no channel and aren't answered."""
        await self._delete(await self.whatsapp_of(ws))

    async def whatsapp_number(self, phone_id: str) -> Channel | None:
        """The business connected to a number: one webhook serves every number, so it's by id."""
        async with self.sessions() as s:
            return await s.scalar(
                select(Channel).where(Channel.kind == WHATSAPP, Channel.address == phone_id)
            )

    def messenger_verify(self, token: str) -> bool:
        """The verify token we set in the Meta app's dashboard, for Messenger's and WhatsApp's
        webhooks. None set: no webhook."""
        expected = self.settings.MESSENGER_VERIFY_TOKEN
        return bool(expected) and secrets.compare_digest(expected, token)

    def messenger_signed(self, body: bytes, signature: str) -> bool:
        """Meta signs each webhook post with our app secret, Messenger's and WhatsApp's. No
        secret: nothing is trusted."""
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
        """Answers one message from Telegram. Runs after the webhook has said OK. A tapped
        button is a callback query: its data becomes the message's text (D99)."""
        message = update.get("message") or {}
        tapped = update.get("callback_query")
        if tapped:
            with contextlib.suppress(TelegramUnavailable):  # its spinner stops on its own
                await self.telegram.answer_callback(channel.token, tapped["id"])
            # only the chat: the message tapped is the bot's own, maybe a photo
            chat = (tapped.get("message") or {}).get("chat")
            message = {"chat": chat, "text": tapped.get("data") or ""}
        chat = message.get("chat") or {}
        if chat.get("type") != "private":
            return  # groups and edits aren't a visitor talking to the desk
        name = " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
        await self._answer(channel, str(chat["id"]), message | {"name": name})

    async def answer_messenger(self, channel: Channel, event: dict[str, Any]) -> None:
        """Answers one message to a Facebook Page. Runs after the webhook has said OK."""
        message = event.get("message")
        if "postback" in event:  # a card's button (D99): its payload is what they chose
            message = {"text": event["postback"].get("payload") or ""}
        if message and message.get("is_echo"):  # sent from the Page: by us, or by the owner
            if message.get("metadata") != OURS and message.get("text"):  # (D135)
                agent = await self.agents.by_id(channel.agent_id)
                buyer = str(event["recipient"]["id"])
                await self.conversations.page_wrote(agent, MESSENGER, buyer, message["text"])
            return
        if not message:
            return
        psid = str(event["sender"]["id"])
        with contextlib.suppress(MessengerUnavailable):  # "Seen", as a person would (D117)
            await self.messenger.mark_seen(channel.token, psid)
        await self._answer(channel, psid, message)

    async def answer_comment(self, channel: Channel, change: dict[str, Any]) -> None:
        """A new comment on the Page's post (D141): one public reply under it and one private
        reply that opens a Messenger chat, once per post and person. The Page's own comments
        (our replies among them) are skipped. Runs after the webhook has said OK."""
        commenter = str((change.get("from") or {}).get("id") or "")
        post_id, comment_id = str(change.get("post_id") or ""), str(change.get("comment_id"))
        if not commenter or commenter == channel.address or not post_id:
            return
        try:
            async with self.sessions() as s, s.begin():
                s.add(
                    PostComment(
                        workspace_id=channel.workspace_id,
                        post_id=post_id,
                        commenter=commenter,
                        comment_id=comment_id,
                        created_at=self.now(),
                    )
                )
        except IntegrityError:  # answered on this post already: the chat has them now
            return
        try:
            agent = await self.agents.by_id(channel.agent_id)
            post = await self.messenger.post_text(channel.token, post_id)
            answer = await self._about_post(channel.workspace_id, agent.config, post)
            with contextlib.suppress(MessengerUnavailable):
                await self.messenger.reply_public(channel.token, comment_id, IN_INBOX)
            psid = await self.messenger.reply_private(channel.token, comment_id, answer)
            post_words = " ".join(post.split())[:200]
            said = f'(a comment on our post: "{post_words}") {change.get("message") or ""}'
            name = (change.get("from") or {}).get("name")
            await self.conversations.commented(agent, psid, said.strip(), answer, name)
        except Exception:
            log.exception("comment not answered", extra={"channel_id": channel.id})

    async def _about_post(self, ws: str, config: Any, post: str) -> str:
        """The private reply: a shop's post's product and price, from its list (rule 1)."""
        if isinstance(config, ShopConfig):
            store: Any = await self.agents.store(ws)
            product = post_product(await store.products(), post)
            if product and product.sold_out():
                return (
                    f"Thanks for your comment! {product.name} is sold out right now. "
                    "Reply here and I'll suggest something similar."
                )
            if product:
                sizes = f", sizes {'/'.join(product.sizes)}" if product.sizes else ""
                price = money(product.price, config.currency)
                return (
                    f"Thanks for your comment! {product.name} is {price}{sizes}. "
                    "Shall I take your order? Just reply here."
                )
        return "Thanks for your comment! Reply here and I'll help you."

    async def answer_whatsapp(
        self, channel: Channel, message: dict[str, Any], name: str | None = None
    ) -> None:
        """Answers one WhatsApp message. Runs after the webhook has said OK. It's put in the
        shape `_answer` reads: a tapped button's id is the text (D99), a photo's caption its
        words; anything else (a sticker, a voice note) has no text."""
        kind = message.get("type")
        said: dict[str, Any] = {}
        if kind == "text":
            said = {"text": (message.get("text") or {}).get("body") or ""}
        elif kind == "interactive":
            tapped = message["interactive"]
            choice = tapped.get("button_reply") or tapped.get("list_reply") or {}
            said = {"text": choice.get("id") or ""}
        elif kind == "image":
            said = {"image": message["image"], "caption": message["image"].get("caption") or ""}
        said["reply_to"] = (message.get("context") or {}).get("id")
        self.whatsapp_said[message["id"]] = said
        if len(self.whatsapp_said) > 10_000:  # the oldest goes
            del self.whatsapp_said[next(iter(self.whatsapp_said))]
        with contextlib.suppress(WhatsAppUnavailable):  # "typing…" until the reply
            await self.whatsapp.send_typing(channel.token, channel.address, message["id"])
        await self._answer(channel, str(message["from"]), said | {"name": name})

    async def _answer(self, channel: Channel, chat_id: str, message: dict[str, Any]) -> None:
        """Nothing here may raise: a failure is logged and the visitor gets a polite line."""
        text = message.get("text")
        try:
            if channel.kind == TELEGRAM and text and text.startswith("/start owner_"):  # D43
                await self.team.link_owner(channel, chat_id, text.removeprefix("/start owner_"))
                return
            if chat_id == channel.owner_chat_id:  # the owner answering a visitor
                await self.team.owner_wrote(channel, message)
                return
            if text is not None and text.split(maxsplit=1)[:1] == ["/start"]:
                agent = await self.agents.by_id(channel.agent_id)
                await self.conversations.restart(agent, channel.kind, chat_id, message.get("name"))
                await self._send(channel, chat_id, agent.greeting)
                return
            # every message counts toward a flood, even one batched into another's turn (I2)
            await self.visitors.arrived(channel.workspace_id, f"{channel.kind}:{chat_id}")
            messages = await self._gather(channel, chat_id, message)
            if messages is not None:
                await self._turn(channel, chat_id, messages)
        except VisitorSilenced:  # blocked, and told once already (I2)
            return
        except HeyDeskError as error:  # too long, or a suspended business: it says why
            await self._send(channel, chat_id, error.message)
        except Exception:
            log.exception("answer failed", extra={"channel_id": channel.id})
            await self._send(channel, chat_id, FAILED)

    async def _gather(
        self, channel: Channel, chat_id: str, message: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        """Many bubbles, one turn (D117): waits `CHAT_WAIT_SECONDS` after a message. If another
        came meanwhile, that one answers both, so this gives None; if not, every message the
        chat sent while it waited, in order."""
        key = f"{channel.id}:{chat_id}"
        waiting = self.waiting.setdefault(key, [])
        waiting.append(message)
        await asyncio.sleep(self.settings.CHAT_WAIT_SECONDS)
        if waiting[-1] is not message:
            return None
        del self.waiting[key]
        return waiting

    async def _turn(self, channel: Channel, chat_id: str, messages: list[dict[str, Any]]) -> None:
        """One turn on the visitor's messages: their words a line each, a quoted message before
        the words that reply to it (D117), and the last photo, theirs or a quoted one."""
        agent = await self.agents.by_id(channel.agent_id)
        takes_photos = agent.desk.takes_photos
        lines: list[str] = []
        photo: bytes | None = None
        for message in messages:
            text = message.get("text")
            sent = await self._photo(channel, message) if takes_photos else None
            if sent is not None:  # its words, if any, are the caption
                photo, text = sent, message.get("caption") or ""
            if text is None:  # a sticker, a voice note, a file
                continue
            words, quoted = await self._quoted(channel, message, takes_photos)
            lines.append(f'(replying to: "{words}") {text}' if words else text)
            if quoted is not None and sent is None:
                photo = quoted
        if not lines:
            await self._send(channel, chat_id, NOT_TEXT)
            return
        typing = asyncio.create_task(self._keep_typing(channel, chat_id))
        try:
            reply = await self.conversations.reply(
                agent,
                channel.kind,
                chat_id,
                "\n".join(line for line in lines if line),
                photo,
                messages[-1].get("name"),
            )
        finally:
            typing.cancel()
        if reply.photos:
            await self._send_photos(
                channel, chat_id, reply.photos, agent.public_key, reply.conversation_id
            )
        # a summary's Yes and Change go under it, the last message (D111)
        summary = reply.card is not None and reply.card.kind == "summary"
        choices = buttons(reply.choices)
        if reply.text:
            await self._send(channel, chat_id, reply.text, [] if summary else choices)
        if reply.card:
            await self._send_card(channel, chat_id, reply.card, choices if summary else [])
        if reply.handoff:
            phone = reply.handoff.phone or reply.handoff.email
            await self._send(
                channel, chat_id, reply.handoff.message + (f"\n{phone}" if phone else "")
            )

    async def _quoted(
        self, channel: Channel, message: dict[str, Any], takes_photos: bool
    ) -> tuple[str, bytes | None]:
        """The earlier message this one replies to (D117): its words, and its photo if the desk
        takes photos. Telegram sends the whole message; Messenger its id, so it's fetched;
        WhatsApp its id, so only a buyer's own message this server kept is known."""
        try:
            if channel.kind == TELEGRAM:
                quoted = message.get("reply_to_message")
            elif channel.kind == MESSENGER:
                mid = (message.get("reply_to") or {}).get("mid")
                quoted = await self.messenger.get_message(channel.token, mid) if mid else None
            else:
                quoted = self.whatsapp_said.get(message.get("reply_to") or "")
            if not quoted:
                return "", None
            words = (quoted.get("text") or quoted.get("caption") or "")[:300]
            photo = await self._photo(channel, quoted) if takes_photos else None
        except (MessengerUnavailable, TelegramUnavailable, WhatsAppUnavailable):
            return "", None  # the reply is read on its own
        return words, photo

    async def _send(
        self, channel: Channel, chat_id: str, text: str, choices: list[str] | None = None
    ) -> None:
        await self.team.send(channel, chat_id, text, choices)

    async def _photo(self, channel: Channel, message: dict[str, Any]) -> bytes | None:
        """The photo a visitor sent, downloaded now (Facebook's URLs expire), or None."""
        if channel.kind == TELEGRAM:  # D63: the same photo in several sizes, largest last
            sizes = message.get("photo")
            return (
                await self.telegram.download(channel.token, sizes[-1]["file_id"]) if sizes else None
            )
        if channel.kind == WHATSAPP:  # D103: a media id, fetched with the token
            image = message.get("image")
            return await self.whatsapp.download(channel.token, image["id"]) if image else None
        url = first_photo(message)  # D62
        return await self.messenger.download(url) if url else None

    async def _send_photos(
        self,
        channel: Channel,
        chat_id: str,
        photos: list[dict[str, str]],
        key: str,
        conversation_id: str = "",
    ) -> None:
        """The products a tool showed, as cards side by side (D125). Messenger: one carousel of
        up to 10, whose photos Meta fetches from us, so their URLs are made whole (D62, D103).
        Telegram and WhatsApp send a message a photo, so the first 3 with a photo go as photos
        (Telegram uploads them, D63) and the rest as one message to tap: a list on WhatsApp,
        a button each on Telegram. Each Order says "Order [code]" (D99); a card links the
        product's page (D121) and its first video (D125). If it fails, the text still goes."""
        cards = [
            {
                **p,
                "choice": f"Order [{p['code']}]",
                "page": self.product_page(key, p["code"], channel.kind, conversation_id),
            }
            for p in photos
        ]
        for c in cards:
            if channel.kind == TELEGRAM and len(c["choice"].encode()) > 64:  # its data limit
                c["choice"] = ""
            if c["url"] and channel.kind != TELEGRAM:
                c["url"] = self.whole(c["url"])
        sent = [c for c in cards if c["url"]][:PHOTOS_SENT]
        rest = [c for c in cards if c not in sent and c["choice"]]
        try:
            if channel.kind == MESSENGER:
                await self.messenger.send_cards(channel.token, chat_id, cards)
            elif channel.kind == WHATSAPP:
                if sent:
                    await self.whatsapp.send_cards(channel.token, channel.address, chat_id, sent)
                if rest:  # a tapped row's id is its words
                    rows = [
                        {"id": c["choice"], "title": c["name"][:24], "description": c["price"]}
                        for c in rest
                    ]
                    await self.whatsapp.send_list(
                        channel.token, channel.address, chat_id, "More to order:", rows
                    )
            else:
                for c in sent:
                    jpeg = await self.photos.read(c["url"])
                    caption = f"{c['name']} · {c['price']}"
                    await self.telegram.send_photo(
                        channel.token, chat_id, jpeg, caption, c["choice"], c["page"], c["video"]
                    )
                if rest:
                    keys = [
                        {"title": f"🛒 {c['name']} · {c['price']}", "choice": c["choice"]}
                        for c in rest
                    ]
                    await self.telegram.send_card(channel.token, chat_id, "More to order:", keys)
        except (
            MessengerUnavailable,
            TelegramUnavailable,
            WhatsAppUnavailable,
            MediaUnavailable,
        ) as error:
            log.warning("photos not sent: %s", error, extra={"channel_id": channel.id})

    async def _send_card(
        self, channel: Channel, chat_id: str, card: Card, choices: list[str]
    ) -> None:
        """A tool's card (D110), after the words, each app its own way (D111): on Telegram
        HTML with buttons; on Messenger and WhatsApp the words with `choices`, a receipt with
        a Pay button, doctors as a carousel or a list. A doctor's Book sends its `book` words.
        If it fails, the words have gone already."""
        text = card_text(card, channel.kind)
        pay_url = card.data.get("pay_url", "")
        doctors = card.data.get("doctors", [])[:10]
        try:
            if channel.kind == TELEGRAM:
                taps = choices + [d["book"] for d in doctors if len(d["book"].encode()) <= 64]
                keys = [{"title": t, "choice": t} for t in taps]
                if pay_url:
                    keys.append({"title": "💳 Pay now", "url": pay_url})
                await self.telegram.send_card(channel.token, chat_id, text, keys)
            elif doctors and channel.kind == MESSENGER:
                cards = [
                    {"name": d["name"], "price": about(d), "choice": d["book"], "button": "Book"}
                    for d in doctors
                ]
                await self.messenger.send_cards(channel.token, chat_id, cards)
            elif doctors:  # WhatsApp: a tapped row's id is its words
                rows = [
                    {"id": d["book"], "title": d["name"][:24], "description": about(d)[:72]}
                    for d in doctors
                ]
                await self.whatsapp.send_list(
                    channel.token, channel.address, chat_id, "Choose a doctor to book.", rows
                )
            elif pay_url and channel.kind == MESSENGER:
                await self.messenger.send_link(channel.token, chat_id, text, "Pay now", pay_url)
            elif pay_url:
                await self.whatsapp.send_link(
                    channel.token, channel.address, chat_id, text, "Pay now", pay_url
                )
            else:
                await self._send(channel, chat_id, text, choices)
        except (MessengerUnavailable, TelegramUnavailable, WhatsAppUnavailable) as error:
            log.warning("card not sent: %s", error, extra={"channel_id": channel.id})

    def product_page(self, key: str, code: str, kind: str, conversation_id: str = "") -> str:
        """A product's public page (D121), on the app it was sent on; `c`, the chat its
        *Buy now* sends the receipt back to (D130)."""
        chat = f"&c={conversation_id}" if conversation_id else ""
        return f"{self.settings.public_url}/p/{key}/{quote(code, safe='')}?from={kind}{chat}"

    async def chat_link(self, ws: str, kind: str) -> str:
        """Back to the business's chat on Messenger or Telegram, from a product's page (D121);
        "" elsewhere: the page was opened over the chat, and closing it goes back."""
        if kind not in (MESSENGER, TELEGRAM):
            return ""
        async with self.sessions() as s:
            channel = await self._find(s, ws, kind)
        if channel is None:
            return ""
        return f"https://{'m.me' if kind == MESSENGER else 't.me'}/{channel.address}"

    def whole(self, url: str) -> str:
        """A photo on this server's disk is "/media/…"; one in R2 is whole already (D101)."""
        return url if url.startswith("https://") else self.settings.public_url + url

    async def _keep_typing(self, channel: Channel, chat_id: str) -> None:
        """ "typing…" lasts five seconds on Telegram; a reply can take longer. WhatsApp's was
        sent once with the message's id (`answer_whatsapp`)."""
        while channel.kind != WHATSAPP:
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
