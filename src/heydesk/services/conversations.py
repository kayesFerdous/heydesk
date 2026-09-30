import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from heydesk.config import Settings
from heydesk.errors import HeyDeskError
from heydesk.ports.llm import ModelSettings
from heydesk.runtime.events import Event, HandOff
from heydesk.runtime.loop import Runtime, TurnInput, run_turn

# ponytail: M1 day-2 stand-ins until the dev agent exists (day 3)
_SYSTEM = "You are the front desk of a small business. Be brief and friendly."
_HANDOFF = HandOff("Sorry, I can't help with that right now. Please call us instead.")


@dataclass
class RunningTurn:
    conversation_id: str
    events: AsyncIterator[Event]


class ConversationService:
    def __init__(self, runtime: Runtime, settings: Settings) -> None:
        self.runtime = runtime
        self.settings = settings

    async def start_turn(
        self,
        public_key: str,
        conversation_id: str | None,
        message: str,
        client_message_id: str,
        channel: str,
    ) -> RunningTurn:
        """The one entry point for every channel.

        ponytail: no agent, DB or lease yet, so every turn starts with an empty
        history. Day 3: the agent by key, the lease, the save. Day 6: the turn
        runs in its own task and listeners subscribe (D3); `client_message_id`
        replays a saved turn (D10).
        """
        message = message.strip()
        if not message or len(message) > self.settings.MAX_MESSAGE_CHARS:
            raise HeyDeskError(f"A message is 1 to {self.settings.MAX_MESSAGE_CHARS} characters.")
        turn = TurnInput(
            system=_SYSTEM,
            tools=[],
            settings=ModelSettings(self.settings.DEFAULT_MODEL),
            history=[],
            user_text=message,
            channel=channel,
            handoff=_HANDOFF,
        )
        return RunningTurn(conversation_id or uuid.uuid4().hex, run_turn(turn, self.runtime))
