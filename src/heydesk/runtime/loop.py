"""One turn: the model talks and calls tools, the visitor sees it streamed (design §6).

A step is one model call. If it asks for tools, they run and their results go
back to the model in the next step; a step with no tool calls is the reply.
Before every step the desk's note on the conversation state (the restaurant's
current order) is added to the system prompt, so the model sees it up to date.
"""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

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
from heydesk.runtime.events import Card, Event, HandOff, Photos, Token, ToolStarted, TurnEnded
from heydesk.runtime.tool import Tool, ToolContext, run_tool
from heydesk.runtime.trace import Trace

MAX_MODEL_CALLS = 6  # rule 9
# Keys a stored message may have for the people who read it, never for the model: who wrote it
# (the team, D43), the photo a visitor sent and the photos a tool showed (D57)
HIDDEN = ("by", "photo", "photos")


@dataclass(frozen=True)
class Runtime:
    """The ports a turn runs against. Built once, in wiring."""

    llm: LanguageModel


@dataclass
class TurnInput:
    system: str  # built for this turn; the desk's note is added before every model call
    tools: list[Tool]  # the desk's tools
    model: str
    history: list[Message]  # the transcript so far
    user_text: str  # what they typed: their yes, if there was one
    channel: str
    agent: AgentConfig
    store: Any  # the desk's data, for its tools
    now: datetime  # when the turn started, in UTC
    state: dict[str, Any] = field(default_factory=dict)  # the conversation state so far
    note: Callable[[dict[str, Any]], str] = lambda state: ""  # the desk's note on the state
    is_first: bool = False  # the conversation's first turn: the greeting goes in first (D8)
    interrupt: asyncio.Event = field(default_factory=asyncio.Event)  # a barge-in sets it
    photo_url: str = ""  # the photo the visitor sent with it, if any (D57)
    photo_line: str = ""  # Python's line on that photo, which the model reads before their text


async def run_turn(turn: TurnInput, rt: Runtime) -> AsyncGenerator[Event]:
    """Every path ends in exactly one `TurnEnded`: no early return, no yield in `finally`."""
    trace = Trace(channel=turn.channel)
    trace.data["llm"] = rt.llm.name
    added = opening_messages(turn, rt.llm)
    state = dict(turn.state)
    heard = state.get("read_back", "")  # the last read-back, from an earlier turn (rule 2)
    tools = {tool.name: tool for tool in turn.tools}
    specs = [tool.spec for tool in turn.tools]
    streamed: list[str] = []  # text sent in this step, for safe_close
    alerts: list[str] = []  # for the owner: an order placed
    used: list[str] = []  # the tools that ran, for the visitor limits (D51)
    said: str | None = None  # a tool's own reply, which ends the turn
    choices: list[str] = []  # the last tool's buttons, for the reply that follows it (D99)
    failed = asked_for_person = interrupted = False

    try:
        for n in range(MAX_MODEL_CALLS):
            finished: Finished | None = None
            calls: list[ToolCall] = []
            system = turn.system + turn.note(state)
            request = ChatRequest(system, specs, for_model([*turn.history, *added]), turn.model)
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
                turn.store,
                state,
                turn.now,
                heard=heard,
                channel=turn.channel,
            )
            outcomes = []
            shown: list[list[dict[str, str]] | None] = []  # each call's photos, if it showed any
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
                used.append(call.name)
                said = said or result.reply
                choices = result.choices or []
                state |= result.state or {}
                asked_for_person |= result.handoff  # the model still replies; the card follows
                if result.alert:
                    alerts.append(result.alert)
                if result.photos:
                    yield Photos(result.photos)
                if result.card and turn.channel != "voice":  # a call has no screen (D110)
                    yield Card(result.card["kind"], result.card["data"])
                shown.append(result.photos)
                outcomes.append(ToolOutcome(call.id, result.ok, result.data))
            results = rt.llm.tool_results(outcomes)  # every call gets its result, even on a stop
            for message, photos in zip(results, shown, strict=False):
                if photos:  # kept for the chat page and the owner, hidden from the model
                    message["photos"] = photos
            added += results
            if said:  # the tool said the reply itself (off_topic): no model call for it
                yield Token(said)
                added.append(rt.llm.assistant_message(said))
                break
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
    yield TurnEnded(
        added,
        state,
        trace.to_dict(),
        handed_off=handoff,
        interrupted=interrupted,
        alerts=alerts,
        tools=used,
        choices=[] if handoff or interrupted else choices,
    )


def opening_messages(turn: TurnInput, llm: LanguageModel) -> list[Message]:
    """This turn's first transcript additions."""
    added = []
    if turn.is_first:  # what they saw first, so the model never greets twice (D8)
        added.append(llm.assistant_message(turn.agent.fill(turn.agent.greeting)))
    said = llm.user_message("\n".join(t for t in (turn.photo_line, turn.user_text) if t))
    if turn.photo_url:
        said["photo"] = turn.photo_url
    added.append(said)
    return added


def for_model(messages: list[Message]) -> list[Message]:
    """The transcript as the model gets it: without the keys only people read (`HIDDEN`)."""
    return [{k: v for k, v in m.items() if k not in HIDDEN} for m in messages]


def safe_close(
    added: list[Message], *, partial: str, closing: str | None, llm: LanguageModel
) -> list[Message]:
    """Keeps the transcript valid for the next turn.

    Text streamed in an unfinished step was never sent back to the model, so it
    becomes a plain assistant message; the closing line (the hand-off) follows.
    """
    tail = [llm.assistant_message(text) for text in (partial, closing) if text]
    return [*added, *tail]
