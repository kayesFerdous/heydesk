"""The team's inbox: the chats listed and counted, and what the team does with one: assign it,
mark it done, snooze it, label it, add notes, reply, take it over or give it back. The
visitor's side, a turn with the agent, is `services/conversations.py`."""

import json
import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, ScalarSelect, String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.errors import Forbidden, InvalidConfig, InvalidTransition, NotFound
from heydesk.models import Conversation, Note, TeamMessage, User
from heydesk.ports.llm import LanguageModel
from heydesk.services.team import HUMAN_STATUS, TEAM_AUTHOR, TeamService, handoff_alert
from heydesk.services.users import CurrentUser
from heydesk.shop.models import ShopOrder

log = logging.getLogger(__name__)

MAX_SNOOZE_DAYS = 30  # the furthest a chat can be snoozed
OPEN_STATUSES = (
    "needs_human",
    "human",
)  # a chat that's open for its team member: waiting, or theirs


class InboxService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        llm: LanguageModel,
        team: TeamService,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.llm = llm  # a team reply is saved in the model's message shape
        self.team = team
        self.now = now

    async def list(
        self,
        workspace_id: str,
        status: str | None = None,
        *,
        channel: str | None = None,
        assigned_to: str | None = None,
        unassigned: bool = False,
        q: str = "",
        done: bool = False,
        label: str = "",
        limit: int = 100,
        before: datetime | None = None,
    ) -> list[Conversation]:
        """Newest activity first, a page at a time: the next page is `before=` the last
        one's `updated_at`. `assigned_to` a team member, or `unassigned`. Chats that are
        done or snoozed are left out, or only the done ones with `done`; a search (`q`: the
        name, or anything said, such as a phone or an order code) looks through all.
        `label`: only the chats with it."""
        query = (
            select(Conversation)
            .where(Conversation.workspace_id == workspace_id)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )
        if status:
            query = query.where(Conversation.status == status)
        if channel:
            query = query.where(Conversation.channel == channel)
        if assigned_to:
            query = query.where(Conversation.assigned_to == assigned_to)
        if unassigned:
            query = query.where(Conversation.assigned_to.is_(None))
        if label:  # the list as JSON holds it in quotes: ["vip", "cod"]
            query = query.where(cast(Conversation.labels, String).contains(json.dumps(label)))
        if q.strip():
            like = (
                "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            )
            query = query.where(
                or_(
                    Conversation.name.ilike(like, escape="\\"),
                    cast(Conversation.messages, String).ilike(like, escape="\\"),
                )
            )
        elif done:
            query = query.where(Conversation.done_at.is_not(None))
        else:
            query = query.where(*self._active_chats_filter())
        if before:
            query = query.where(Conversation.updated_at < before)
        async with self.sessions() as s:
            return list(await s.scalars(query))

    async def counts(self, workspace_id: str, user_id: str) -> dict[str, int]:
        """How many shown chats wait for a person: all of them, the caller's, and no one's.
        And the orders and messages still new, so the inbox counts them without listing them."""
        waiting = Conversation.status == "needs_human"

        def new(table: type[ShopOrder | TeamMessage]) -> ScalarSelect[int]:
            where = table.workspace_id == workspace_id, table.status == "new"
            return select(func.count()).where(*where).scalar_subquery()

        query = select(
            func.count().filter(waiting),
            func.count().filter(waiting, Conversation.assigned_to == user_id),
            func.count().filter(waiting, Conversation.assigned_to.is_(None)),
            new(ShopOrder),
            new(TeamMessage),
        ).where(Conversation.workspace_id == workspace_id, *self._active_chats_filter())
        async with self.sessions() as s:
            needs_human, mine, unassigned, orders, messages = (await s.execute(query)).one()
        return {
            "needs_human": needs_human,
            "mine": mine,
            "unassigned": unassigned,
            "orders_new": orders,
            "messages_new": messages,
        }

    async def open_chats(self, workspace_id: str) -> dict[str, int]:
        """Each team member's open chats, the number a hand-off goes by; none: not there."""
        query = (
            select(Conversation.assigned_to, func.count())
            .where(
                Conversation.workspace_id == workspace_id,
                Conversation.assigned_to.is_not(None),
                Conversation.status.in_(OPEN_STATUSES),
            )
            .group_by(Conversation.assigned_to)
        )
        async with self.sessions() as s:
            return {user_id: n for user_id, n in (await s.execute(query)).all() if user_id}

    async def get(self, workspace_id: str, conversation_id: str) -> Conversation:
        async with self.sessions() as s:
            return await get_conversation(s, workspace_id, conversation_id)

    async def latest_for_visitor(self, workspace_id: str, key: str) -> Conversation | None:
        """The visitor's latest conversation (`key` as `visitors.visitor_key` makes it), for the
        blocked list."""
        kind, contact = key.split(":", 1)
        channels = ("web", "voice") if kind == "web" else (kind,)
        async with self.sessions() as s:
            return await s.scalar(
                select(Conversation)
                .where(
                    Conversation.workspace_id == workspace_id,
                    Conversation.channel.in_(channels),
                    Conversation.contact == contact,
                )
                .order_by(Conversation.updated_at.desc())
                .limit(1)
            )

    async def assign(
        self, workspace_id: str, conversation_id: str, by: CurrentUser, user_id: str | None
    ) -> Conversation:
        """Who has the chat: the owner (or an admin) gives any chat to anyone on the team,
        or no one; a moderator takes an unassigned one for themself and gives back their own."""
        async with self.sessions() as s, s.begin():
            conversation = await get_conversation(s, workspace_id, conversation_id)
            if by.role == "moderator":
                takes = user_id == by.user_id and conversation.assigned_to in (None, by.user_id)
                gives_back = user_id is None and conversation.assigned_to in (None, by.user_id)
                if not (takes or gives_back):
                    raise Forbidden("Only the owner can give a chat to someone else.")
            if user_id is not None:
                member = select(User.id).where(
                    User.id == user_id, User.workspace_id == workspace_id
                )
                if await s.scalar(member) is None:
                    raise NotFound("There's no such member on your team.")
            conversation.assigned_to = user_id
        log.info("assigned", extra={"conversation_id": conversation.id})
        if user_id is not None and user_id != by.user_id:  # given to them: tell them
            await self.team.alert(
                conversation, handoff_alert(conversation.messages, "👉 This chat is yours now")
            )
        return conversation

    async def mark_done(self, workspace_id: str, conversation_id: str, done: bool) -> Conversation:
        """Done hides the chat until the visitor writes again; not done shows it again."""
        async with self.sessions() as s, s.begin():
            conversation = await get_conversation(s, workspace_id, conversation_id)
            conversation.done_at = self.now() if done else None
            if done:
                conversation.snoozed_until = None
        return conversation

    async def snooze(
        self, workspace_id: str, conversation_id: str, until: datetime | None
    ) -> Conversation:
        """Hides the chat until `until`, when it comes back with an alert; None: back now.
        A time not in the future, or more than `MAX_SNOOZE_DAYS` ahead, is refused."""
        now = self.now()
        if until is not None and not now < until <= now + timedelta(days=MAX_SNOOZE_DAYS):
            raise InvalidConfig({"until": f"Pick a time in the next {MAX_SNOOZE_DAYS} days."})
        async with self.sessions() as s, s.begin():
            conversation = await get_conversation(s, workspace_id, conversation_id)
            conversation.snoozed_until = until
            conversation.done_at = None
        return conversation

    async def wake_snoozed(self) -> None:
        """Each snoozed chat that's due is back in the list, and the owner hears about it."""
        async with self.sessions() as s, s.begin():
            due = list(
                await s.scalars(
                    select(Conversation).where(Conversation.snoozed_until <= self.now())
                )
            )
            for conversation in due:
                conversation.snoozed_until = None
        for conversation in due:
            await self.team.alert(conversation, "⏰ A chat you snoozed is back.")
            log.info("snooze over", extra={"conversation_id": conversation.id})

    async def hand_back(self, workspace_id: str, conversation_id: str) -> Conversation:
        """The owner dealt with a hand-off from the console: the agent answers again, as
        after `/done` on Telegram."""
        async with self.sessions() as s, s.begin():
            conversation = await get_conversation(s, workspace_id, conversation_id)
            if conversation.status == "active":
                raise InvalidTransition("This conversation isn't waiting for a person.")
            conversation.status, conversation.taken_over_at = "active", None
        log.info("hand-off handled", extra={"conversation_id": conversation.id})
        return conversation

    async def set_labels(
        self, workspace_id: str, conversation_id: str, labels: Sequence[str]
    ) -> Conversation:
        """The team's own words for a chat: "vip", "fake order". Kept in order, once
        each; the visitor and the model never see them."""
        async with self.sessions() as s, s.begin():
            conversation = await get_conversation(s, workspace_id, conversation_id)
            kept = dict.fromkeys(" ".join(lb.split()).lower() for lb in labels if lb.strip())
            conversation.labels = [*kept]
        return conversation

    async def notes(self, workspace_id: str, conversation_id: str) -> Sequence[Note]:
        """The team's notes on a chat, oldest first."""
        async with self.sessions() as s:
            await get_conversation(s, workspace_id, conversation_id)  # 404 for another business's
            rows = await s.scalars(
                select(Note)
                .where(Note.workspace_id == workspace_id, Note.conversation_id == conversation_id)
                .order_by(Note.created_at)
            )
            return rows.all()

    async def add_note(
        self, workspace_id: str, conversation_id: str, author: str, text: str
    ) -> Note:
        """A note for the team: its own table, added and never edited (rule 5), and
        never in the transcript, so the visitor and the model don't see it."""
        async with self.sessions() as s, s.begin():
            await get_conversation(s, workspace_id, conversation_id)
            note = Note(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                author=author,
                text=text.strip(),
                created_at=self.now(),
            )
            s.add(note)
        return note

    async def team_reply(self, workspace_id: str, conversation_id: str, text: str) -> Conversation:
        """The team answers from the inbox, as from a Telegram alert: the words go to
        the visitor and into the transcript as the team's, and the chat is taken over, until
        given back or 30 quiet minutes. A call can't take one."""
        async with self.sessions() as s, s.begin():
            conversation = await get_conversation(s, workspace_id, conversation_id, lock=True)
            if conversation.channel == "voice":
                raise InvalidTransition("That was a phone call: call them back instead.")
            said = {**self.llm.assistant_message(text.strip()), "by": TEAM_AUTHOR}
            conversation.messages = [
                *conversation.messages,
                said,
            ]  # a new list, so SQLAlchemy sees it
            conversation.status, conversation.taken_over_at, conversation.updated_at = (
                HUMAN_STATUS,
                self.now(),
                self.now(),
            )
        await self.team.send_to_visitor(conversation, text.strip())
        log.info("team replied", extra={"conversation_id": conversation.id})
        return conversation

    async def take_over(self, workspace_id: str, conversation_id: str, on: bool) -> Conversation:
        """The team takes the chat, and the agent stays quiet in it, or gives it back:
        the inbox's switch, "." in Business Suite or on Telegram. Quiet for
        TAKEOVER_QUIET_MINUTES, it's the agent's again."""
        async with self.sessions() as s, s.begin():
            conversation = await get_conversation(s, workspace_id, conversation_id)
            conversation.status = HUMAN_STATUS if on else "active"
            conversation.taken_over_at = self.now() if on else None
        log.info("taken over" if on else "given back", extra={"conversation_id": conversation.id})
        return conversation

    def _active_chats_filter(self) -> tuple[ColumnElement[bool], ...]:
        """The usual list's chats: not done, and not snoozed."""
        return (
            Conversation.done_at.is_(None),
            or_(Conversation.snoozed_until.is_(None), Conversation.snoozed_until <= self.now()),
        )

    async def by_ids(self, workspace_id: str, ids: set[str]) -> Sequence[Conversation]:
        """These of the business's conversations (the tickets' chats), in no order."""
        if not ids:
            return []
        async with self.sessions() as s:
            found = await s.scalars(
                select(Conversation).where(
                    Conversation.workspace_id == workspace_id, Conversation.id.in_(ids)
                )
            )
            return found.all()


async def get_conversation(
    s: AsyncSession, workspace_id: str, conversation_id: str, *, lock: bool = False
) -> Conversation:
    """`lock` before adding to `messages`: two writes at once would each add to the list
    they read, and the later one would drop the other's line. Postgres holds the row
    until the transaction ends; SQLite writes one transaction at a time anyway."""
    query = select(Conversation).where(
        Conversation.id == conversation_id, Conversation.workspace_id == workspace_id
    )
    conversation = await s.scalar(query.with_for_update() if lock else query)
    if conversation is None:
        raise NotFound("There's no such conversation. Start a new one.")
    return conversation


async def least_busy(s: AsyncSession, workspace_id: str) -> str | None:
    """The joined, available moderator with the fewest open chats (waiting for a person, or with
    the team), the longest on the team first when it's even; None with no one."""
    open_chats = (
        select(func.count())
        .select_from(Conversation)
        .where(
            Conversation.workspace_id == workspace_id,
            Conversation.assigned_to == User.id,
            Conversation.status.in_(OPEN_STATUSES),
        )
        .correlate(User)
        .scalar_subquery()
    )
    moderators = select(User.id).where(
        User.workspace_id == workspace_id,
        User.role == "moderator",
        User.email_verified_at.is_not(None),
        User.available,
    )
    return await s.scalar(moderators.order_by(open_chats, User.created_at).limit(1))
