"""Messaging apps: a business connects its own Telegram bot, Facebook Page or
WhatsApp number, and the agent answers there.

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
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.db import new_id
from heydesk.desks.shop.tools import product_cards
from heydesk.desks.shop.values import ShopConfig, post_products, price_range
from heydesk.errors import (
    HeyDeskError,
    InvalidConfig,
    NotFound,
    UnreadablePhoto,
    VisitorSilenced,
)
from heydesk.models import Channel, PagePost, PostComment, Turn, User
from heydesk.ports.media import MediaUnavailable
from heydesk.ports.messenger import OURS, MessengerApi, MessengerUnavailable
from heydesk.ports.telegram import TelegramApi, TelegramUnavailable
from heydesk.ports.transcriber import Transcriber, TranscriberUnavailable
from heydesk.ports.whatsapp import WhatsAppApi, WhatsAppUnavailable
from heydesk.runtime.events import Card
from heydesk.runtime.speech import money
from heydesk.services.agents import AgentService
from heydesk.services.conversations import ConversationService
from heydesk.services.photos import PhotoService, to_jpeg
from heydesk.services.team import MESSENGER, TELEGRAM, WHATSAPP, TeamService
from heydesk.services.visitors import VisitorService

log = logging.getLogger(__name__)

NOT_TEXT = "I can only read text messages for now. Please type your question."
FAILED = "Sorry, something went wrong. Please try again."
# Under a comment, for everyone; the details go privately
IN_INBOX = "Sent you the details in Messenger 📩"
LOOK_IN_INBOX = "We sent you a message in Messenger: check your inbox 📩"  # the nudge
NUDGE = timedelta(hours=2)  # no answer in the chat this long after a comment's reply
MAX_VOICE_BYTES = 2_000_000  # a voice note bigger than this (minutes long) isn't listened to
PHOTOS_SENT = 3  # on Telegram and WhatsApp a photo is a message; more cards are one list


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

    async def connect_facebook(self, ws: str, user_token: str, page_id: str) -> Channel:
        """Connects one of the login's Pages with its own token, as if typed in."""
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
        the account and the number. Connecting again replaces the old number."""
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
        self, ws: str, code: str, account_id: str, phone_id: str
    ) -> Channel:
        """Embedded Signup's code becomes the business's token, the number is registered on
        the Cloud API, then it's connected as if typed in."""
        self.whatsapp_login()  # the app's id, secret and configuration are set
        taken = await self.whatsapp_number(phone_id)
        if taken is not None and taken.workspace_id != ws:
            raise InvalidConfig({"phone_id": "This number is connected to another business."})
        try:
            token = await self.whatsapp.exchange(
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
        return await self.connect_whatsapp(ws, token, account_id, phone_id)

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
            if message.get("metadata") != OURS and message.get("text"):
                agent = await self.agents.by_id(channel.agent_id)
                buyer = str(event["recipient"]["id"])
                await self.conversations.page_wrote(agent, MESSENGER, buyer, message["text"])
            return
        if not message:
            return
        psid = str(event["sender"]["id"])
        with contextlib.suppress(MessengerUnavailable):  # "Seen", as a person would
            await self.messenger.mark_seen(channel.token, psid)
        await self._answer(channel, psid, message)

    async def answer_comment(self, channel: Channel, change: dict[str, Any]) -> None:
        """A new comment on the Page's post: one public reply under it and one private
        reply that opens a Messenger chat, once per post and person. The Page's own comments
        (our replies among them) are skipped. Runs after the webhook has said OK."""
        commenter = str((change.get("from") or {}).get("id") or "")
        post_id, comment_id = str(change.get("post_id") or ""), str(change.get("comment_id"))
        if not commenter or commenter == channel.address or not post_id:
            return
        agent = await self.agents.by_id(channel.agent_id)
        if agent.config.ai_off:
            return
        try:
            async with self.sessions() as s, s.begin():
                first = PostComment(
                    workspace_id=channel.workspace_id,
                    post_id=post_id,
                    commenter=commenter,
                    comment_id=comment_id,
                    created_at=self.now(),
                )
                s.add(first)
        except IntegrityError:  # answered on this post already: the chat has them now
            return
        try:
            post = await self.page_post(channel, post_id)
            products = await self._post_products(channel.workspace_id, agent.config, post)
            answer, cards, public = self._about_post(agent, products)
            with contextlib.suppress(MessengerUnavailable):
                await self.messenger.reply_public(channel.token, comment_id, public)
            try:
                psid = await self.messenger.reply_private(channel.token, comment_id, answer, cards)
            except MessengerUnavailable:
                if not cards:
                    raise
                psid = await self.messenger.reply_private(channel.token, comment_id, answer)
            post_words = " ".join(post.text.split())[:200]
            said = f'(a comment on our post: "{post_words}") {change.get("message") or ""}'
            name = (change.get("from") or {}).get("name")
            conv = await self.conversations.handle_post_comment(
                agent, psid, said.strip(), answer, name
            )
            async with self.sessions() as s, s.begin():
                saved = await s.get_one(PostComment, first.id)
                saved.conversation_id = conv
        except Exception:
            log.exception("comment not answered", extra={"channel_id": channel.id})

    async def page_post(self, channel: Channel, post_id: str) -> PagePost:
        """What the post is about, found at its first comment and kept: every later
        comment uses it again, so the post is fetched, and its photo searched, once."""
        async with self.sessions() as s:
            kept = await s.scalar(
                select(PagePost).where(
                    PagePost.workspace_id == channel.workspace_id, PagePost.post_id == post_id
                )
            )
        if kept is not None:
            return kept
        fetched = await self.messenger.get_post(channel.token, post_id)
        agent = await self.agents.by_id(channel.agent_id)
        codes: list[str] = []
        if isinstance(agent.config, ShopConfig):
            store: Any = await self.agents.store(channel.workspace_id)
            listed = await store.products()
            codes = post_products(listed, fetched["text"])
            if not codes and fetched["picture"]:  # no product named: its photo's closest one
                codes = await self._photo_product(channel, listed, fetched["picture"])
        post = PagePost(
            workspace_id=channel.workspace_id,
            post_id=post_id,
            text=fetched["text"][:2000],
            products=codes,
            created_at=self.now(),
        )
        try:
            async with self.sessions() as s, s.begin():
                s.add(post)
        except IntegrityError:  # two first comments at once: the other one kept it
            pass
        return post

    async def _photo_product(self, channel: Channel, listed: list[Any], url: str) -> list[str]:
        """The one product whose photo is most like the post's, or none."""
        try:
            jpeg = to_jpeg(await self.messenger.download(url))
        except (MessengerUnavailable, UnreadablePhoto):
            return []
        matches = await self.photos.nearest(channel.workspace_id, jpeg) or []
        by_url = {photo: p.code for p in listed for photo in p.photos}
        return next(([by_url[u]] for u, _ in matches if u in by_url), [])

    async def _post_products(self, ws: str, config: Any, post: PagePost) -> list[Any]:
        """The post's products as the list has them now: today's price and stock."""
        if not post.products or not isinstance(config, ShopConfig):
            return []
        store: Any = await self.agents.store(ws)
        listed = {p.code: p for p in await store.products()}
        return [listed[code] for code in post.products if code in listed]

    def _about_post(
        self, agent: Any, products: list[Any]
    ) -> tuple[str, list[dict[str, str]] | None, str]:
        """The private reply's words, its cards when the post has many products, and
        the public reply: the price, or the range, from the list (rule 1)."""
        currency = getattr(agent.config, "currency", "BDT")
        if not products:
            return "Thanks for your comment! Reply here and I'll help you.", None, IN_INBOX
        public = f"{price_range(products, currency)}. {IN_INBOX}"
        if len(products) > 1:
            cards = [
                {
                    **card,
                    "choice": f"Order [{card['code']}]",
                    "page": self.product_page(agent.public_key, card["code"], MESSENGER),
                    "url": self.absolute_url(card["url"]) if card["url"] else "",
                }
                for card in product_cards(products, currency)
            ]
            names = ", ".join(f"{c['name']} {c['price']}" for c in cards)
            return f"Thanks for your comment! This post has: {names}.", cards, public
        product = products[0]
        if product.sold_out():
            answer = (
                f"Thanks for your comment! {product.name} is sold out right now. "
                "Reply here and I'll suggest something similar."
            )
            return answer, None, IN_INBOX
        sizes = f", sizes {'/'.join(product.sizes)}" if product.sizes else ""
        price = money(product.price, currency)
        answer = (
            f"Thanks for your comment! {product.name} is {price}{sizes}. "
            "Shall I take your order? Just reply here."
        )
        return answer, None, public

    async def nudge_comments(self) -> None:
        """No answer in the chat ~2 hours after a comment's private reply: one more
        reply under the comment, once. A day on, it's left alone."""
        now = self.now()
        async with self.sessions() as s, s.begin():
            due = list(
                await s.scalars(
                    select(PostComment).where(
                        PostComment.nudged.is_(False),
                        PostComment.conversation_id.is_not(None),
                        PostComment.created_at.between(now - timedelta(days=1), now - NUDGE),
                    )
                )
            )
            quiet = []
            for comment in due:
                comment.nudged = True  # tried, sent or not: once
                answered = await s.scalar(
                    select(Turn.id).where(
                        Turn.conversation_id == comment.conversation_id,
                        Turn.at >= comment.created_at,
                    )
                )
                if answered is None:
                    quiet.append(comment)
        for comment in quiet:
            async with self.sessions() as s:
                channel = await self._find(s, comment.workspace_id, MESSENGER)
            if channel is not None:
                with contextlib.suppress(MessengerUnavailable):
                    await self.messenger.reply_public(
                        channel.token, comment.comment_id, LOOK_IN_INBOX
                    )

    async def run(self, every: float) -> None:
        """The comment nudge round every `every` seconds, until cancelled. The first
        time round it also sets the Telegram bots up again."""
        refreshed = False
        while True:
            await asyncio.sleep(every)
            if not refreshed:
                refreshed = True
                try:
                    await self.refresh_telegram()
                except Exception:
                    log.exception("telegram webhooks not refreshed")
            try:
                await self.nudge_comments()
            except Exception:
                log.exception("comment nudges failed")

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
                await self.team.team_wrote(channel, chat_id, message)
                return
            if text is not None and text.split(maxsplit=1)[:1] == ["/start"]:
                agent = await self.agents.by_id(channel.agent_id)
                await self.conversations.restart(agent, channel.kind, chat_id, message.get("name"))
                await self._send(channel, chat_id, agent.greeting)
                return
            # every message counts toward a flood, even one batched into another's turn
            await self.visitors.count_message(channel.workspace_id, f"{channel.kind}:{chat_id}")
            messages = await self._gather(channel, chat_id, message)
            if messages is not None:
                await self._turn(channel, chat_id, messages)
        except VisitorSilenced:  # blocked, and told once already
            return
        except HeyDeskError as error:  # too long, or a suspended business: it says why
            await self._send(channel, chat_id, error.message)
        except Exception:
            log.exception("answer failed", extra={"channel_id": channel.id})
            await self._send(channel, chat_id, FAILED)

    async def _gather(
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

    async def _turn(self, channel: Channel, chat_id: str, messages: list[dict[str, Any]]) -> None:
        """One turn on the visitor's messages: their words a line each, a quoted message before
        the words that reply to it, and the last photo, theirs or a quoted one."""
        agent = await self.agents.by_id(channel.agent_id)
        takes_photos = agent.desk.takes_photos
        lines: list[str] = []
        photo: bytes | None = None
        for message in messages:
            text = message.get("text")
            sent = await self._photo(channel, message) if takes_photos else None
            if sent is not None:  # its words, if any, are the caption
                photo, text = sent, message.get("caption") or ""
            if text is None:
                text = await self._voice(channel, message)
            if text is None:  # a sticker, a file, a voice note not understood
                continue
            words, quoted = await self._quoted(channel, message, takes_photos)
            lines.append(f'(replying to: "{words}") {text}' if words else text)
            if quoted is not None and sent is None:
                photo = quoted
        if not lines:
            await self._send(channel, chat_id, NOT_TEXT)
            return
        typing = None  # "typing…" only when the agent will answer, not the team
        if await self.conversations.answers(agent, channel.kind, chat_id):
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

    async def _quoted(
        self, channel: Channel, message: dict[str, Any], takes_photos: bool
    ) -> tuple[str, bytes | None]:
        """The earlier message this one replies to: its words, and its photo if the desk
        takes photos. Telegram sends the whole message; Messenger its id, so it's fetched;
        WhatsApp its id, so only a message this server kept is known: the buyer's own, or a
        product card the agent sent."""
        try:
            if channel.kind == TELEGRAM:  # a product photo's caption names it
                quoted = message.get("reply_to_message")
            elif channel.kind == MESSENGER:
                mid = (message.get("reply_to") or {}).get("mid")
                if mid in self.cards_sent:
                    return self.cards_sent[mid][:300], None
                quoted = await self.messenger.get_message(channel.token, mid) if mid else None
            else:
                reply_to = message.get("reply_to") or ""
                if reply_to in self.cards_sent:
                    return self.cards_sent[reply_to][:300], None
                quoted = self.whatsapp_said.get(reply_to)
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
        sent = [c for c in cards if c["url"]][:PHOTOS_SENT]
        rest = [c for c in cards if c not in sent and c["choice"]]
        try:
            if channel.kind == MESSENGER:  # one carousel: a reply to it is to all of them
                mid = await self.messenger.send_cards(channel.token, chat_id, cards)
                self._remember_cards([mid], ["; ".join(card_words(c) for c in cards)])
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
                    jpeg = await self.photos.read(c["url"])
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

    async def chat_link(self, ws: str, kind: str) -> str:
        """Back to the business's chat on Messenger or Telegram, from a product's page;
        "" elsewhere: the page was opened over the chat, and closing it goes back."""
        if kind not in (MESSENGER, TELEGRAM):
            return ""
        async with self.sessions() as s:
            channel = await self._find(s, ws, kind)
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
            if kind == TELEGRAM:  # and so does each moderator
                await s.execute(
                    update(User).where(User.workspace_id == ws).values(telegram_chat_id=None)
                )
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
