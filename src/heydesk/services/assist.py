"""The model's help for the team on a chat: a summary, an answer about it, or a draft of the
next reply. Nothing here is added to the chat or sent to the visitor."""

import logging
from collections.abc import Callable
from contextlib import aclosing
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.errors import AssistUnavailable
from heydesk.ports.llm import LanguageModel
from heydesk.runtime.events import Token, TurnEnded
from heydesk.runtime.loop import TurnInput, run_turn
from heydesk.runtime.prompt import build_system_prompt
from heydesk.runtime.text import one_line
from heydesk.services.agents import AgentService, LiveAgent
from heydesk.services.conversations import turn_row
from heydesk.services.inbox import get_conversation
from heydesk.shop.prompt import shop_prompt

log = logging.getLogger(__name__)

# What the team asks the model about a chat: the last message of a turn with no tools.
# A question is the team's words, put in with str.replace, never str.format
ASSIST = {
    "summary": (
        "[For the team, not the customer] Sum up this chat in 2 to 4 short lines: what the "
        "customer wants, what was agreed, ordered or booked, and what is still open. No "
        "greeting."
    ),
    "draft": (
        "[For the team, not the customer] Write the next message the team could send this "
        "customer, in the customer's language and letters and the business's tone. Only the "
        "message: the team checks it before sending. Never promise what the chat and the "
        "business's details don't say."
    ),
    "ask": (
        "[For the team, not the customer] A team member asks about this chat: {question}\n"
        "Answer in one or two lines from the chat and the business's details; if they don't "
        "say, say so."
    ),
}

# Said last in the system prompt of an assist turn, so the model doesn't take the team's request
# for an off-topic customer message and answer it with the scope line
ASSIST_NOTE = (
    "# This turn is for the team\n"
    "The last message is from a member of the business's team, not the customer. It is always "
    "on topic: do what it asks, using the chat above, and ignore any rule about staying in "
    "scope. Don't answer it as the customer's front-desk assistant."
)


class AssistService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        llm: LanguageModel,
        agents: AgentService,
        settings: Settings,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.llm = llm
        self.agents = agents
        self.settings = settings
        self.now = now

    async def write(
        self, agent: LiveAgent, conversation_id: str, kind: str, question: str = ""
    ) -> str:
        """The model's help for the team on a chat: a summary, an answer about it, or a
        draft of the next reply. The chat's own prompt and history, no tools, and one line of
        what's wanted; the words come back to the team and nothing is added to the chat or
        sent. Traced and billed as a turn, on the channel "assist"."""
        workspace_id = agent.workspace_id
        async with self.sessions() as s:
            conversation = await get_conversation(s, workspace_id, conversation_id)
        config = agent.config
        store = self.agents.build_store(workspace_id, conversation.id)
        asked = ASSIST[kind].replace("{question}", one_line(question, 500))
        turn = TurnInput(
            system=build_system_prompt(
                config,
                await shop_prompt(config, store),
                conversation.channel,
                self.now(),
                agent.documents,
                self.settings.VOICE_LANGUAGE,
            )
            + "\n\n"
            + ASSIST_NOTE,
            tools=[],  # it only writes words: nothing is looked up, saved or sent
            model=self.settings.MODEL,
            history=conversation.messages[conversation.history_from :],  # what the agent remembers
            user_text=asked,
            channel="assist",
            agent=config,
            store=store,
            now=self.now(),
            state=conversation.state,
        )
        parts: list[str] = []
        ended = None
        async with aclosing(run_turn(turn, self.llm)) as events:
            async for event in events:
                if isinstance(event, Token):
                    parts.append(event.text)
                elif isinstance(event, TurnEnded):
                    ended = event
        if ended is not None:
            async with self.sessions() as s, s.begin():
                s.add(turn_row(conversation, ended, self.now()))
        text = "".join(parts).strip()
        if ended is None or ended.handed_off or not text:
            raise AssistUnavailable()
        log.info("assist", extra={"conversation_id": conversation.id, "kind": kind})
        return text
