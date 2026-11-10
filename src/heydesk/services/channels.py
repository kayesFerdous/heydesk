"""Messaging apps: the agent answers a business's own Telegram bot, Facebook Page or
WhatsApp number (connecting them is `services/channel_links.py`).

The app posts each message to our webhook. The agent waits a moment for the visitor's next
message, then answers all of them in one turn, sent as one message.
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

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.errors import (
    HeyDeskError,
    NotFound,
    VisitorSilenced,
)
from heydesk.models import Channel
from heydesk.ports.media import MediaUnavailable
from heydesk.ports.messenger import OUR_APP_TAG, MessengerApi, MessengerUnavailable
from heydesk.ports.telegram import TelegramApi, TelegramUnavailable
from heydesk.ports.transcriber import Transcriber, TranscriberUnavailable
from heydesk.ports.whatsapp import WhatsAppApi, WhatsAppUnavailable
from heydesk.runtime.events import Card
from heydesk.services.agents import AgentService
from heydesk.services.channel_links import find_channel
from heydesk.services.conversations import ConversationService
from heydesk.services.photos import PhotoService
from heydesk.services.team import MESSENGER, TELEGRAM, WHATSAPP, TeamService
from heydesk.services.visitors import VisitorService

log = logging.getLogger(__name__)

NOT_TEXT_REPLY = "I can only read text messages for now. Please type your question."
FAILED_REPLY = "Sorry, something went wrong. Please try again."
MAX_VOICE_BYTES = 2_000_000  # a voice note bigger than this (minutes long) isn't listened to
MAX_PHOTO_MESSAGES = 3  # on Telegram and WhatsApp a photo is a message; more cards are one list


def first_photo(message: dict[str, Any]) -> str | None:
    """The first image's URL in a Messenger message. A sticker is an image too, so it's not."""
    for attachment in message.get("attachments") or []:
        payload = attachment.get("payload") or {}
        if attachment.get("type") == "image" and "sticker_id" not in payload:
            return payload.get("url")
    return None


def first_audio(message: dict[str, Any]) -> str | None:
    """The first voice clip's URL in a Messenger message."""
    for attachment in message.get("attachments") or []:
        if attachment.get("type") == "audio":
            return (attachment.get("payload") or {}).get("url")
    return None


def buttons(choices: list[str]) -> list[str]:
    """The choices a button can carry: Messenger takes 13 quick replies of 20 characters,
    and Telegram's button data is 64 bytes. A longer one is left out, so it's typed instead."""
    return [c for c in choices if len(c) <= 20 and len(c.encode()) <= 64][:13]


def card_text(card: Card, channel: str) -> str:
    """A card as a message's words, a line each, its title in bold: Telegram's HTML
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
        marks = [step_icon(s, i == len(steps) - 1 and len(steps) > 1) for i, s in enumerate(steps)]
        said.append(bold(esc(f"Order {data['number']}")))
        said.append(" → ".join(f"{m} {esc(s['label'])}" for m, s in zip(marks, steps, strict=True)))
        if data["note"]:
            said.append(esc(data["note"]))
        return "\n".join(said)
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
    if "order_url" in data:  # the apps make a link of it
        said.append(esc(f"Follow your order: {data['order_url']}"))
    return "\n".join(said)


def step_icon(step: dict[str, Any], last: bool) -> str:
    """✅ done, 🔵 where it is now, ⚪ still to come; the last step reached is done."""
    if step["current"] and not last:
        return "🔵"
    return "✅" if step["done"] else "⚪"


def card_words(card: dict[str, str]) -> str:
    """A product card as the buyer sees it: "Cotton Shirt · ৳950"."""
    return f"{card['name']} · {card['price']}"


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
        transcriber: Transcriber | None = None,
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.transcriber = transcriber  # voice notes in words; None: "please type"
        self.conversations = conversations
        self.visitors = visitors
        self.photos = photos
        self.team = team
        self.telegram = telegram
        self.messenger = messenger
        self.whatsapp = whatsapp
        self.settings = settings
        self.now = now
        # a chat's messages not answered yet, while it waits for more; in memory, so
        # one server process
        self.waiting: dict[str, list[dict[str, Any]]] = {}
        # WhatsApp's webhook quotes only a message's id, so the buyers' recent messages are
        # kept here, by id, for their replies
        self.whatsapp_said: dict[str, dict[str, Any]] = {}
        # the product cards the agent sent on Messenger and WhatsApp, by message id, as
        # "Cotton Shirt · ৳950": a buyer's reply to one names the product
        self.cards_sent: dict[str, str] = {}

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

    async def telegram_webhook(self, channel_id: str, secret: str) -> Channel:
        """The channel a webhook call is for. 404 unless the secret is the one we gave Telegram."""
        async with self.sessions() as s:
            channel = await s.get(Channel, channel_id)
        if channel is None or not secrets.compare_digest(channel.secret, secret):
            raise NotFound("Not found")
        return channel

    async def answer_telegram(self, channel: Channel, update: dict[str, Any]) -> None:
        """Answers one message from Telegram. Runs after the webhook has said OK. A tapped
        button is a callback query: its data becomes the message's text."""
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
        if "postback" in event:  # a card's button: its payload is what they chose
            message = {"text": event["postback"].get("payload") or ""}
        if message and message.get("is_echo"):  # sent from the Page: by us, or by the owner
            if message.get("metadata") != OUR_APP_TAG and message.get("text"):
                agent = await self.agents.by_id(channel.agent_id)
                buyer = str(event["recipient"]["id"])
                await self.conversations.save_page_message(agent, MESSENGER, buyer, message["text"])
            return
        if not message:
            return
        chat_id = str(event["sender"]["id"])
        with contextlib.suppress(MessengerUnavailable):  # "Seen", as a person would
            await self.messenger.mark_seen(channel.token, chat_id)
        await self._answer(channel, chat_id, message)

    async def answer_whatsapp(
        self, channel: Channel, message: dict[str, Any], name: str | None = None
    ) -> None:
        """Answers one WhatsApp message. Runs after the webhook has said OK. It's put in the
        shape `_answer` reads: a tapped button's id is the text, a photo's caption its
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
        elif kind == "audio":  # a voice note
            said = {"audio": message["audio"]}
        said["reply_to"] = (message.get("context") or {}).get("id")
        said["id"] = message["id"]  # "typing…" goes with it (`_keep_typing`)
        self.whatsapp_said[message["id"]] = said
        if len(self.whatsapp_said) > 10_000:  # the oldest goes
            del self.whatsapp_said[next(iter(self.whatsapp_said))]
        await self._answer(channel, str(message["from"]), said | {"name": name})

    async def _answer(self, channel: Channel, chat_id: str, message: dict[str, Any]) -> None:
        """Nothing here may raise: a failure is logged and the visitor gets a polite line."""
        text = message.get("text")
        try:
            if channel.kind == TELEGRAM and text and text.startswith("/start owner_"):
                await self.team.link_owner(channel, chat_id, text.removeprefix("/start owner_"))
                return
            if channel.kind == TELEGRAM and text and text.startswith("/start member_"):
                await self.team.link_member(channel, chat_id, text.removeprefix("/start member_"))
                return
            if chat_id == channel.owner_chat_id or (
                channel.kind == TELEGRAM and await self.team.is_team(channel, chat_id)
            ):  # the owner or a moderator answering a visitor
                await self.team.handle_team_reply(channel, chat_id, message)
                return
            if text is not None and text.split(maxsplit=1)[:1] == ["/start"]:
                agent = await self.agents.by_id(channel.agent_id)
                await self.conversations.restart(agent, channel.kind, chat_id, message.get("name"))
                await self._send(channel, chat_id, agent.greeting)
                return
            # every message counts toward a flood, even one batched into another's turn
            await self.visitors.count_message(channel.workspace_id, f"{channel.kind}:{chat_id}")
            messages = await self._collect_messages(channel, chat_id, message)
            if messages is not None:
                await self._run_turn(channel, chat_id, messages)
        except VisitorSilenced:  # blocked, and told once already
            return
        except HeyDeskError as error:  # too long, or a suspended business: it says why
            await self._send(channel, chat_id, error.message)
        except Exception:
            log.exception("answer failed", extra={"channel_id": channel.id})
            await self._send(channel, chat_id, FAILED_REPLY)

    async def _collect_messages(
        self, channel: Channel, chat_id: str, message: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        """Many bubbles, one turn: waits `CHAT_WAIT_SECONDS` after a message. If another
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

    async def _run_turn(
        self, channel: Channel, chat_id: str, messages: list[dict[str, Any]]
    ) -> None:
        """One turn on the visitor's messages: their words a line each, a quoted message before
        the words that reply to it, and the last photo, theirs or a quoted one."""
        agent = await self.agents.by_id(channel.agent_id)
        lines: list[str] = []
        photo: bytes | None = None
        for message in messages:
            text = message.get("text")
            sent = await self._photo(channel, message)
            if sent is not None:  # its words, if any, are the caption
                photo, text = sent, message.get("caption") or ""
            if text is None:
                text = await self._voice(channel, message)
            if text is None:  # a sticker, a file, a voice note not understood
                continue
            words, quoted = await self._replied_to_message(channel, message)
            lines.append(f'(replying to: "{words}") {text}' if words else text)
            if quoted is not None and sent is None:
                photo = quoted
        if not lines:
            await self._send(channel, chat_id, NOT_TEXT_REPLY)
            return
        typing = None  # "typing…" only when the agent will answer, not the team
        if await self.conversations.will_answer(agent, channel.kind, chat_id):
            typing = asyncio.create_task(
                self._keep_typing(channel, chat_id, messages[-1].get("id", ""))
            )
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
            if typing is not None:
                typing.cancel()
        if reply.photos:
            await self._send_photos(
                channel, chat_id, reply.photos, agent.public_key, reply.conversation_id
            )
        # a summary's Yes and Change go under it, the last message
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

    def _remember_cards(self, ids: list[str], words: list[str]) -> None:
        for message_id, said in zip(ids, words, strict=False):
            if message_id:
                self.cards_sent[message_id] = said
        while len(self.cards_sent) > 10_000:  # the oldest go
            del self.cards_sent[next(iter(self.cards_sent))]

    async def _replied_to_message(
        self, channel: Channel, message: dict[str, Any]
    ) -> tuple[str, bytes | None]:
        """The earlier message this one replies to: its words and its photo. Telegram sends the whole message; Messenger its id, so it's fetched;
        WhatsApp its id, so only a message this server kept is known: the buyer's own, or a
        product card the agent sent."""
        try:
            if channel.kind == TELEGRAM:  # a product photo's caption names it
                quoted = message.get("reply_to_message")
            elif channel.kind == MESSENGER:
                message_id = (message.get("reply_to") or {}).get("mid")
                if message_id in self.cards_sent:
                    return self.cards_sent[message_id][:300], None
                quoted = (
                    await self.messenger.get_message(channel.token, message_id)
                    if message_id
                    else None
                )
            else:
                reply_to = message.get("reply_to") or ""
                if reply_to in self.cards_sent:
                    return self.cards_sent[reply_to][:300], None
                quoted = self.whatsapp_said.get(reply_to)
            if not quoted:
                return "", None
            words = (quoted.get("text") or quoted.get("caption") or "")[:300]
            photo = await self._photo(channel, quoted)
        except (MessengerUnavailable, TelegramUnavailable, WhatsAppUnavailable):
            return "", None  # the reply is read on its own
        return words, photo

    async def _send(
        self, channel: Channel, chat_id: str, text: str, choices: list[str] | None = None
    ) -> None:
        await self.team.send(channel, chat_id, text, choices)

    async def _voice(self, channel: Channel, message: dict[str, Any]) -> str | None:
        """A voice note's words, marked as heard, or None: not a voice note, too long,
        or nothing understood."""
        if self.transcriber is None:
            return None
        try:
            if channel.kind == TELEGRAM:
                note = message.get("voice") or message.get("audio")
                audio = (
                    await self.telegram.download(channel.token, note["file_id"]) if note else b""
                )
                name = "voice.ogg"
            elif channel.kind == WHATSAPP:
                note = message.get("audio")
                audio = await self.whatsapp.download(channel.token, note["id"]) if note else b""
                name = "voice.ogg"
            else:
                url = first_audio(message)
                audio = await self.messenger.download(url) if url else b""
                name = "voice.mp4"
            if not audio or len(audio) > MAX_VOICE_BYTES:
                return None
            words = await self.transcriber.transcribe(audio, name)
        except (TelegramUnavailable, MessengerUnavailable, WhatsAppUnavailable):
            return None
        except TranscriberUnavailable as error:
            log.warning("voice note failed: %s", error, extra={"channel_id": channel.id})
            return None
        return f"[Voice note] {words}" if words else None

    async def _photo(self, channel: Channel, message: dict[str, Any]) -> bytes | None:
        """The photo a visitor sent, downloaded now (Facebook's URLs expire), or None."""
        if channel.kind == TELEGRAM:  # the same photo in several sizes, largest last
            sizes = message.get("photo")
            return (
                await self.telegram.download(channel.token, sizes[-1]["file_id"]) if sizes else None
            )
        if channel.kind == WHATSAPP:  # a media id, fetched with the token
            image = message.get("image")
            return await self.whatsapp.download(channel.token, image["id"]) if image else None
        url = first_photo(message)
        return await self.messenger.download(url) if url else None

    async def _send_photos(
        self,
        channel: Channel,
        chat_id: str,
        photos: list[dict[str, str]],
        key: str,
        conversation_id: str = "",
    ) -> None:
        """The products a tool showed, as cards side by side. Messenger: one carousel of
        up to 10, whose photos Meta fetches from us, so their URLs are made whole.
        Telegram and WhatsApp send a message a photo, so the first 3 with a photo go as photos
        (Telegram uploads them) and the rest as one message to tap: a list on WhatsApp,
        a button each on Telegram. Each Order says "Order [code]"; a card links the
        product's page and its first video. If it fails, the text still goes."""
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
                c["url"] = self.absolute_url(c["url"])
        sent = [c for c in cards if c["url"]][:MAX_PHOTO_MESSAGES]
        rest = [c for c in cards if c not in sent and c["choice"]]
        try:
            if channel.kind == MESSENGER:  # one carousel: a reply to it is to all of them
                message_id = await self.messenger.send_cards(channel.token, chat_id, cards)
                self._remember_cards([message_id], ["; ".join(card_words(c) for c in cards)])
            elif channel.kind == WHATSAPP:
                if sent:
                    ids = await self.whatsapp.send_cards(
                        channel.token, channel.address, chat_id, sent
                    )
                    self._remember_cards(ids, [card_words(c) for c in sent])
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
                    jpeg = await self.photos.read_jpeg(c["url"])
                    caption = card_words(c)
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
        """A tool's card, after the words, each app its own way: on Telegram
        HTML with buttons; on Messenger and WhatsApp the words with `choices`, a receipt with
        a Pay button. If it fails, the words have gone already."""
        text = card_text(card, channel.kind)
        pay_url = card.data.get("pay_url", "")
        try:
            if channel.kind == TELEGRAM:
                keys = [{"title": t, "choice": t} for t in choices]
                if pay_url:
                    keys.append({"title": "💳 Pay now", "url": pay_url})
                await self.telegram.send_card(channel.token, chat_id, text, keys)
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
        """A product's public page: `from`, only where the page shows a button back to
        the app (Messenger, Telegram); `c`, the chat its *Buy now* sends the receipt back to.
        Elsewhere it's only `/p/<key>/<code>`."""
        query = ([f"from={kind}"] if kind in (MESSENGER, TELEGRAM) else []) + (
            [f"c={conversation_id}"] if conversation_id else []
        )
        link = f"{self.settings.public_url}/p/{key}/{quote(code, safe='')}"
        return link + ("?" + "&".join(query) if query else "")

    async def chat_link(self, workspace_id: str, kind: str) -> str:
        """Back to the business's chat on Messenger or Telegram, from a product's page;
        "" elsewhere: the page was opened over the chat, and closing it goes back."""
        if kind not in (MESSENGER, TELEGRAM):
            return ""
        async with self.sessions() as s:
            channel = await find_channel(s, workspace_id, kind)
        if channel is None:
            return ""
        return f"https://{'m.me' if kind == MESSENGER else 't.me'}/{channel.address}"

    def absolute_url(self, url: str) -> str:
        """A photo on this server's disk is "/media/…"; one in R2 is whole already."""
        return url if url.startswith("https://") else self.settings.public_url + url

    async def _keep_typing(self, channel: Channel, chat_id: str, message_id: str) -> None:
        """ "typing…" lasts five seconds on Telegram; a reply can take longer. WhatsApp's lasts
        until the reply, so it's sent once, with their last message's id."""
        while True:
            try:
                if channel.kind == WHATSAPP:
                    await self.whatsapp.send_typing(channel.token, channel.address, message_id)
                    return
                if channel.kind == MESSENGER:
                    await self.messenger.send_typing(channel.token, chat_id)
                else:
                    await self.telegram.send_typing(channel.token, chat_id)
            except (TelegramUnavailable, MessengerUnavailable, WhatsAppUnavailable):
                return
            await asyncio.sleep(4)
