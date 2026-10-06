"""One turn: the model talks and calls tools, the visitor sees it streamed (design §6).

A step is one model call. If it asks for tools, they run and their results go
back to the model in the next step; a step with no tool calls is the reply.
Before every step the state note is added to the system prompt, so the model
always sees what's known and what to ask next.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import datetime
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
from heydesk.runtime.prompt import state_note
from heydesk.runtime.tasks import Task
from heydesk.runtime.tool import (
    Appointments,
    Catalog,
    Knowledge,
    Orders,
    Requests,
    Tool,
    ToolContext,
    run_tool,
)
from heydesk.runtime.trace import Trace

MAX_MODEL_CALLS = 6  # rule 9


@dataclass(frozen=True)
class Runtime:
    """The ports a turn runs against. Built once, in wiring."""

    llm: LanguageModel
    decider: Decider


@dataclass
class TurnInput:
    system: str  # built for this turn; the state note is added before every model call
    tools: list[Tool]  # the agent's enabled tools
    model: str
    history: list[Message]  # the transcript so far
    user_text: str
    channel: str
    agent: AgentConfig
    items: Catalog  # the agent's items, for the tools
    knowledge: Knowledge  # the agent's documents, for search_knowledge
    orders: Orders  # where place_order saves
    requests: Requests  # where submit_request saves
    appointments: Appointments  # what the clinic's cancel and reschedule tools change
    tasks: dict[str, Task]  # the preset's tasks
    now: datetime  # when the turn started, in UTC
    state: dict[str, Any] = field(default_factory=dict)  # the conversation state so far
    is_first: bool = False  # the conversation's first turn: the greeting goes in first (D8)
    interrupt: asyncio.Event = field(default_factory=asyncio.Event)  # a barge-in sets it


async def run_turn(turn: TurnInput, rt: Runtime) -> AsyncGenerator[Event]:
    """Every path ends in exactly one `TurnEnded`: no early return, no yield in `finally`."""
    trace = Trace(channel=turn.channel)
    trace.data["llm"] = rt.llm.name
    added = opening_messages(turn, rt.llm)
    agent_said = last_agent_text([*turn.history, *added])  # what the visitor is replying to
    state = dict(turn.state)
    tools = {tool.name: tool for tool in turn.tools}
    specs = [tool.spec for tool in turn.tools]
    streamed: list[str] = []  # text sent in this step, for safe_close
    failed = asked_for_person = interrupted = False

    try:
        for n in range(MAX_MODEL_CALLS):
            finished: Finished | None = None
            calls: list[ToolCall] = []
            system = turn.system + state_note(turn.tasks, state, turn.agent.currency)
            request = ChatRequest(system, specs, [*turn.history, *added], turn.model)
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
                failed = True
                break
            added.append(finished.assistant_message)
            streamed = []  # that text is in the transcript now
            if not calls:
                break  # the reply is done

            ctx = ToolContext(
                turn.agent,
                state,
                turn.items,
                turn.knowledge,
                turn.tasks,
                turn.now,
                rt.decider,
                turn.orders,
                turn.requests,
                turn.appointments,
                agent_said=agent_said,
                visitor_said=turn.user_text,
            )
            outcomes = []
            for call in calls:
                tool = tools.get(call.name)
                yield (
                    ToolStarted(call.name, tool.label, tool.filler)
                    if tool
                    else ToolStarted(call.name)
                )
                with trace.step("tool", tool=call.name) as step:
                    result = await run_tool(tool, call, ctx)
                    step["ok"] = result.ok
                yield ToolFinished(call.name, result.ok)
                state |= result.state or {}
                asked_for_person |= result.handoff  # the model still replies; the card follows
                outcomes.append(ToolOutcome(call.id, result.ok, result.data))
            added += rt.llm.tool_results(outcomes)  # every call gets its result, even on a stop
            if turn.interrupt.is_set():
                interrupted = True
                break
        else:
            trace.data["error"] = f"still calling tools after {MAX_MODEL_CALLS} model calls"
            failed = True
    except LLMUnavailable as error:
        trace.data["error"] = str(error)
        failed = True

    contact = turn.agent.handoff
    if failed or interrupted:  # a failure ends with the hand-off message as a polite line
        closing = contact.message if failed else None
        added = safe_close(added, partial="".join(streamed), closing=closing, llm=rt.llm)
    handoff = failed or asked_for_person
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


def last_agent_text(messages: list[Message]) -> str:
    """The text of the last assistant message that has some."""
    for m in reversed(messages):
        if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"]:
            return str(m["content"])
    return ""


def safe_close(
    added: list[Message], *, partial: str, closing: str | None, llm: LanguageModel
) -> list[Message]:
    """Keeps the transcript valid for the next turn.

    Text streamed in an unfinished step was never sent back to the model, so it
    becomes a plain assistant message; the closing line (the hand-off) follows.
    """
    tail = [llm.assistant_message(text) for text in (partial, closing) if text]
    return [*added, *tail]
