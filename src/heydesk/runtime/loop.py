"""One turn: the model talks, the visitor sees it streamed (02 §7).

Text only for now. Tools and the step loop arrive on M1 day 5, the progress
policy on day 6, the hand-off guard on M2 day 8.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass, field

from heydesk.ports.decider import Decider
from heydesk.ports.llm import (
    ChatRequest,
    Finished,
    LanguageModel,
    LLMUnavailable,
    Message,
    ModelSettings,
    TextDelta,
    ToolSpec,
)
from heydesk.runtime.events import Event, HandOff, Token, TurnEnded
from heydesk.runtime.trace import Trace


@dataclass(frozen=True)
class Runtime:
    """The ports a turn runs against. Built once, in wiring."""

    llm: LanguageModel
    decider: Decider


@dataclass
class TurnInput:
    system: str  # built at publish; sent byte-for-byte
    tools: list[ToolSpec]
    settings: ModelSettings
    history: list[Message]  # the transcript so far
    user_text: str
    channel: str
    handoff: HandOff
    interrupt: asyncio.Event = field(default_factory=asyncio.Event)  # a barge-in sets it


async def run_turn(turn: TurnInput, rt: Runtime) -> AsyncIterator[Event]:
    """Every path ends in exactly one `TurnEnded`: no early return, no yield in `finally`."""
    trace = Trace(channel=turn.channel)
    added = [rt.llm.user_message(turn.user_text)]
    streamed: list[str] = []  # text sent in this step, for safe_close
    handoff = interrupted = False
    finished: Finished | None = None

    try:
        request = ChatRequest(turn.system, turn.tools, [*turn.history, *added], turn.settings)
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

    if handoff or interrupted:
        closing = turn.handoff.message if handoff else None
        added = safe_close(added, partial="".join(streamed), closing=closing, llm=rt.llm)
    if handoff:
        yield turn.handoff
    trace.data["interrupted"] = interrupted
    yield TurnEnded(added, trace.to_dict(), handed_off=handoff, interrupted=interrupted)


def safe_close(
    added: list[Message], *, partial: str, closing: str | None, llm: LanguageModel
) -> list[Message]:
    """Keeps the transcript valid for the next turn.

    Text streamed in an unfinished step was never sent back to the model, so it
    becomes a plain assistant message; the closing line (the hand-off) follows.
    """
    tail = [llm.assistant_message(text) for text in (partial, closing) if text]
    return [*added, *tail]
