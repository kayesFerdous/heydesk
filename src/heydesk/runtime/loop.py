"""One turn: the model talks and calls tools, the visitor sees it streamed (design §6).

A step is one model call. If it asks for tools, they run and their results go
back to the model in the next step; a step with no tool calls is the reply.
The state note arrives on day 6.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any

from heydesk.ports.decider import Decider
from heydesk.ports.llm import (
    ChatRequest,
    Finished,
    LanguageModel,
    LLMUnavailable,
    Message,
    TextDelta,
    ToolCall,
    ToolOutcome,
)
from heydesk.runtime.config import AgentConfig
from heydesk.runtime.events import Event, HandOff, Token, ToolFinished, ToolStarted, TurnEnded
from heydesk.runtime.tool import Catalog, Tool, ToolContext, run_tool
from heydesk.runtime.trace import Trace

MAX_MODEL_CALLS = 6  # rule 9


@dataclass(frozen=True)
class Runtime:
    """The ports a turn runs against. Built once, in wiring."""

    llm: LanguageModel
    decider: Decider


@dataclass
class TurnInput:
    system: str  # built for this turn
    tools: list[Tool]  # the agent's enabled tools
    model: str
    history: list[Message]  # the transcript so far
    user_text: str
    channel: str
    agent: AgentConfig
    items: Catalog  # the agent's items, for the tools
    state: dict[str, Any] = field(default_factory=dict)  # the conversation state so far
    is_first: bool = False  # the conversation's first turn: the greeting goes in first (D8)
    interrupt: asyncio.Event = field(default_factory=asyncio.Event)  # a barge-in sets it


async def run_turn(turn: TurnInput, rt: Runtime) -> AsyncGenerator[Event]:
    """Every path ends in exactly one `TurnEnded`: no early return, no yield in `finally`."""
    trace = Trace(channel=turn.channel)
    trace.data["llm"] = rt.llm.name
    added = opening_messages(turn, rt.llm)
    state = dict(turn.state)
    tools = {tool.name: tool for tool in turn.tools}
    specs = [tool.spec for tool in turn.tools]
    streamed: list[str] = []  # text sent in this step, for safe_close
    handoff = interrupted = False

    try:
        for n in range(MAX_MODEL_CALLS):
            finished: Finished | None = None
            calls: list[ToolCall] = []
            request = ChatRequest(turn.system, specs, [*turn.history, *added], turn.model)
            with trace.step("model", n=n) as step:
                async with aclosing(rt.llm.stream(request)) as stream:
                    async for event in stream:
                        if isinstance(event, TextDelta):
                            if turn.interrupt.is_set():
                                interrupted = True
                                break  # closes the model stream
                            trace.first_output(step)
                            streamed.append(event.text)
                            yield Token(event.text)
                        elif isinstance(event, ToolCall):
                            calls.append(event)
                        elif isinstance(event, Finished):
                            finished = event
                if finished:
                    step["stop"] = finished.stop
                    trace.add_usage(step, finished.usage)
            if interrupted:
                break
            # A refusal, a lost stream, or a step with neither words nor tools is a failure
            if finished is None or finished.stop == "refusal" or not (streamed or calls):
                trace.data["error"] = f"no usable reply (stop: {finished and finished.stop})"
                handoff = True
                break
            added.append(finished.assistant_message)
            streamed = []  # that text is in the transcript now
            if not calls:
                break  # the reply is done

            ctx = ToolContext(agent=turn.agent, state=state, items=turn.items)
            outcomes = []
            for call in calls:
                tool = tools.get(call.name)
                yield ToolStarted(call.name, tool.label if tool else "")
                with trace.step("tool", tool=call.name) as step:
                    result = await run_tool(tool, call, ctx)
                    step["ok"] = result.ok
                yield ToolFinished(call.name, result.ok)
                state |= result.state or {}
                outcomes.append(ToolOutcome(call.id, result.ok, result.data))
            added += rt.llm.tool_results(outcomes)  # every call gets its result, even on a stop
            if turn.interrupt.is_set():
                interrupted = True
                break
        else:
            trace.data["error"] = f"still calling tools after {MAX_MODEL_CALLS} model calls"
            handoff = True
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
    yield TurnEnded(added, state, trace.to_dict(), handed_off=handoff, interrupted=interrupted)


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
