"""One turn: the model talks, the visitor sees it streamed (design §6).

Text only for now. Tools and the step loop arrive on day 5, the state note on
day 6.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass, field

from heydesk.ports.decider import Decider
from heydesk.ports.llm import (
    ChatRequest,
    Finished,
    LanguageModel,
    LLMUnavailable,
    Message,
    TextDelta,
    ToolSpec,
)
from heydesk.runtime.config import AgentConfig
from heydesk.runtime.events import Event, HandOff, Token, TurnEnded
from heydesk.runtime.trace import Trace


@dataclass(frozen=True)
class Runtime:
    """The ports a turn runs against. Built once, in wiring."""

    llm: LanguageModel
    decider: Decider


@dataclass
class TurnInput:
    system: str  # built for this turn
    tools: list[ToolSpec]
    model: str
    history: list[Message]  # the transcript so far
    user_text: str
    channel: str
    agent: AgentConfig
    is_first: bool = False  # the conversation's first turn: the greeting goes in first (D8)
    interrupt: asyncio.Event = field(default_factory=asyncio.Event)  # a barge-in sets it


async def run_turn(turn: TurnInput, rt: Runtime) -> AsyncGenerator[Event]:
    """Every path ends in exactly one `TurnEnded`: no early return, no yield in `finally`."""
    trace = Trace(channel=turn.channel)
    trace.data["llm"] = rt.llm.name
    added = opening_messages(turn, rt.llm)
    streamed: list[str] = []  # text sent in this step, for safe_close
    handoff = interrupted = False
    finished: Finished | None = None

    try:
        request = ChatRequest(turn.system, turn.tools, [*turn.history, *added], turn.model)
        with trace.step("model", n=0) as step:
            async with aclosing(rt.llm.stream(request)) as stream:
                async for event in stream:
                    if isinstance(event, TextDelta):
                        if turn.interrupt.is_set():
                            interrupted = True
                            break  # closes the model stream
                        trace.first_output(step)
                        streamed.append(event.text)
                        yield Token(event.text)
                    elif isinstance(event, Finished):
                        finished = event
            if finished:
                step["stop"] = finished.stop
                trace.add_usage(step, finished.usage)
        if not interrupted:
            # A refusal, a tool call (none can run yet) or an empty reply is a failure
            if finished is None or finished.stop in ("refusal", "tool_use") or not streamed:
                trace.data["error"] = f"no usable reply (stop: {finished and finished.stop})"
                handoff = True
            else:
                added.append(finished.assistant_message)
                streamed = []  # that text is in the transcript now
    except LLMUnavailable as error:
        trace.data["error"] = str(error)
        handoff = True

    contact = turn.agent.handoff
    if handoff or interrupted:
        closing = contact.message if handoff else None
        added = safe_close(added, partial="".join(streamed), closing=closing, llm=rt.llm)
    if handoff:
        yield HandOff(contact.message, contact.phone, contact.email)
    trace.data["interrupted"] = interrupted
    yield TurnEnded(added, trace.to_dict(), handed_off=handoff, interrupted=interrupted)


def opening_messages(turn: TurnInput, llm: LanguageModel) -> list[Message]:
    """This turn's first transcript additions."""
    added = []
    if turn.is_first:  # what they saw first, so the model never greets twice (D8)
        added.append(llm.assistant_message(turn.agent.fill(turn.agent.greeting)))
    added.append(llm.user_message(turn.user_text))
    return added


def safe_close(
    added: list[Message], *, partial: str, closing: str | None, llm: LanguageModel
) -> list[Message]:
    """Keeps the transcript valid for the next turn.

    Text streamed in an unfinished step was never sent back to the model, so it
    becomes a plain assistant message; the closing line (the hand-off) follows.
    """
    tail = [llm.assistant_message(text) for text in (partial, closing) if text]
    return [*added, *tail]
