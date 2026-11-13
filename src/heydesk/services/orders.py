"""The shop's orders once they're placed, paid or moved: who hears of it, and what follows.

An order comes from a chat (the agent's tools), from a product's page (*Buy now*) or from the
team, who type one for a buyer who called. Whichever it was, the same things follow, and they
are here so that they can't differ: the alert goes to whoever has the order's chat, or to the
owner when it has none, and the order goes into a linked WooCommerce store.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.domain.values import Confirmed
from heydesk.errors import Conflict, InvalidConfig
from heydesk.models import Agent, Conversation, Turn
from heydesk.runtime import cards
from heydesk.runtime.events import Card
from heydesk.runtime.text import one_line
from heydesk.services.agents import AgentService, LiveAgent
from heydesk.services.card_text import card_text
from heydesk.services.team import TeamService, conversation_in
from heydesk.services.woo import WooService
from heydesk.shop.checkout import TeamOrder, checked_order, order_alert
from heydesk.shop.models import ShopOrder
from heydesk.shop.orders import STATUS_WORDS, Coupon, NewOrder, lines_text
from heydesk.shop.store import OutOfStock, ShopStore

log = logging.getLogger(__name__)

# A shop order read back and never said yes to gets one nudge this long after
REMINDER_AFTER, REMINDER_BEFORE = timedelta(hours=1), timedelta(hours=3)
ORDER_REMINDER = "Shall I confirm your order? Tap Yes, or tell me what to change."


class OrderService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        agents: AgentService,
        team: TeamService,
        woo: WooService,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.team = team
        self.woo = woo
        self.now = now

    # What follows an order

    async def alert(self, workspace_id: str, conversation: Conversation | None, text: str) -> None:
        """To whoever has the order's chat. An order from a product page opened from no chat,
        or typed by the team, has none: the owner hears of it."""
        if conversation is None:
            await self.team.notify_owner(workspace_id, text)
        else:
            await self.team.alert_team(conversation, text)

    async def order_saved(
        self, workspace_id: str, conversation: Conversation | None, alert: str
    ) -> None:
        """An order was placed or paid: its alert, then into a linked WooCommerce store now."""
        await self.alert(workspace_id, conversation, alert)
        self.woo.push_soon(workspace_id)

    async def conversation_of(self, order: ShopOrder) -> Conversation | None:
        """The chat an order was placed in, or None."""
        if order.conversation_id is None:
            return None
        async with self.sessions() as s:
            return await s.scalar(conversation_in(order.workspace_id, order.conversation_id))

    # An order from a product's page

    async def page_store(
        self, agent: LiveAgent, conversation_id: str
    ) -> tuple[ShopStore, Conversation | None]:
        """The agent's data for an order from a product's page: linked to the chat the
        page was opened from when `conversation_id` is one of this agent's, else to none."""
        conversation = None
        if conversation_id:
            async with self.sessions() as s:
                conversation = await s.scalar(
                    conversation_in(agent.workspace_id, conversation_id).where(
                        Conversation.agent_id == agent.agent_id
                    )
                )
        store = self.agents.build_store(
            agent.workspace_id, conversation.id if conversation else None
        )
        return store, conversation

    async def place_from_page(
        self,
        store: ShopStore,
        conversation: Conversation | None,
        order: Confirmed[NewOrder],
        read_back: str,
        how: str,
    ) -> ShopOrder:
        """Saves the page's order after the buyer's yes. The chat it came from gets the
        receipt; the owner hears of it as of a chat's order, an unpaid one once it's paid.
        Raises OutOfStock, and saves nothing, if it sold out since they checked."""
        saved = await store.place_order(order, "page")
        workspace_id = store.workspace_id
        if conversation:
            receipt = cards.receipt(
                "Order placed",
                saved.code,
                STATUS_WORDS[saved.status].capitalize(),
                [cards.row("Items", lines_text(saved.lines)), cards.row("Payment", how)],
                cards.amount(saved.total, saved.currency),
                order_url=store.order_link(saved.id),
            )
            said = card_text(Card(receipt["kind"], receipt["data"]), "web")  # plain words
            await self.team.tell_visitor(workspace_id, conversation.id, said)
        if saved.status != "unpaid":  # the owner hears of an unpaid one once it's paid
            alert = await order_alert(store, saved.code, read_back, saved.phone, saved.lines)
            await self.order_saved(workspace_id, conversation, alert)
        log.info("page order", extra={"workspace_id": workspace_id, "order_id": saved.id})
        return saved

    # The team's side

    async def add_team_order(self, store: ShopStore, order: TeamOrder) -> ShopOrder:
        """An order the team types for a buyer who called: checked and added up as the
        chat's are, then saved `confirmed` with no yes asked, since the team member is the yes.
        The owner hears of it as of any new order."""
        workspace_id = store.workspace_id
        config = (await self.agents.by_workspace(workspace_id)).config
        checked = await checked_order(store, config, order.lines, order)
        if isinstance(checked, str):
            raise InvalidConfig({"order": checked})
        new, read_back, _ = checked
        try:
            saved = await store.add_team_order(new)
        except OutOfStock as error:
            raise Conflict(f"Not enough in stock: {error}.") from None
        alert = await order_alert(store, saved.code, read_back, saved.phone, saved.lines)
        alert = alert.replace("New order", "New order, typed by the team", 1)
        await self.order_saved(workspace_id, None, alert)
        return saved

    async def move(
        self,
        store: ShopStore,
        order_id: str,
        status: str | None,
        paid_in: bool | None = None,
        reason: str | None = None,
    ) -> ShopOrder:
        """The owner moves an order along; cancelling puts its items back in stock. The buyer
        hears it in their chat when it's confirmed, shipped or cancelled, as the team's
        message. Once it ended, whether the money came in and why it came back, too."""
        found = await store.get(ShopOrder, order_id)
        moved = await store.move_order(order_id, status or found.status)
        if (
            moved.status != found.status
            and moved.conversation_id
            and (line := moved.status_update_text())
        ):
            await self.team.tell_visitor(store.workspace_id, moved.conversation_id, line)
        if paid_in is not None or reason is not None:
            moved = await store.record_outcome(order_id, paid_in, reason)
        return moved

    async def send_offer(
        self, store: ShopStore, text: str, kind: str, value: int, ends_at: datetime, hours: int
    ) -> tuple[str, int]:
        """Sends the offer to each chat where the buyer wrote in the last `hours`, inside the
        app's window (`tell_visitor_if_allowed`: Messenger and WhatsApp only within 24 hours),
        and saves it as a coupon only those chats may use, until `ends_at`. Returns the
        coupon's code and how many chats got it."""
        now = store.now()
        if ends_at <= now:
            raise InvalidConfig({"ends_at": "Pick an end time that's still to come."})
        code = f"OFFER{await store.offers_made() + 1}"
        try:
            coupon = Coupon.model_validate(
                {"code": code, "kind": kind, "value": value, "ends_at": ends_at}
            )
        except ValueError as error:
            raise InvalidConfig({"value": str(error)}) from None
        text = one_line(text)
        sent = [
            chat
            for chat in await store.conversations_since(now - timedelta(hours=hours))
            if await self.team.tell_visitor_if_allowed(store.workspace_id, chat, text)
        ]
        await store.add_offer(coupon, sent)
        log.info("offer sent", extra={"workspace_id": store.workspace_id, "chats": len(sent)})
        return code, len(sent)

    # A background job

    async def remind_unconfirmed_orders(self) -> None:
        """A shop order read back and never said yes to, 1–3 hours on: the chat's last
        turn ran `place_order` and no order came of it. One nudge per chat, ever, with the
        read-back's buttons, and only inside the app's window (`tell_visitor_if_allowed`)."""
        now = self.now()
        due = []
        async with self.sessions() as s, s.begin():
            quiet = await s.scalars(
                select(Conversation)
                .join(Agent, Agent.id == Conversation.agent_id)
                .where(
                    Conversation.status == "active",
                    Conversation.nudged.is_(False),
                    Conversation.updated_at.between(now - REMINDER_BEFORE, now - REMINDER_AFTER),
                )
            )
            for conversation in quiet.all():
                last = await s.scalar(
                    select(Turn)
                    .where(Turn.conversation_id == conversation.id, Turn.channel != "assist")
                    .order_by(Turn.at.desc())
                    .limit(1)
                )
                # Limitation: a place_order that only asked for a missing detail counts too
                if last is None or "place_order" not in last.tools:
                    continue
                placed = await s.scalar(
                    select(ShopOrder.id).where(
                        ShopOrder.workspace_id == conversation.workspace_id,
                        ShopOrder.conversation_id == conversation.id,
                        ShopOrder.created_at >= last.at,
                    )
                )
                if placed is None:
                    conversation.nudged = True  # tried, sent or not: never twice
                    due.append((conversation.workspace_id, conversation.id))
        for workspace_id, conversation_id in due:
            await self.team.tell_visitor_if_allowed(
                workspace_id, conversation_id, ORDER_REMINDER, ["Yes", "Change"]
            )
            log.info("nudged", extra={"conversation_id": conversation_id})
