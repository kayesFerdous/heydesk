"""The commit check (design §7): read back, then a yes, then save (rule 2).

One Jev call with two yes/no questions. Both likely → a `Confirmed[T]`.
Anything else, Jev being down included, → None: nothing is saved.
"""

import logging

from heydesk.domain.values import Confirmed
from heydesk.ports.decider import Decider, DeciderUnavailable, Question, YesNo
from heydesk.runtime.tool import ToolResult

log = logging.getLogger(__name__)

THRESHOLD = 0.7  # a "hmm, maybe" must not pass; tune it from the logged answers

QUESTIONS: dict[str, Question] = {
    "read_back": YesNo(
        "Does agent_last_message repeat the details in details_to_confirm back to the visitor?"
    ),
    "said_yes": YesNo("Is visitor_reply a clear yes to agent_last_message?"),
}


async def confirm[T](
    decider: Decider, value: T, details: str, agent_said: str, visitor_said: str
) -> Confirmed[T] | None:
    """`details` is what must have been read back, as the visitor would hear it."""
    state = {
        "details_to_confirm": details,
        "agent_last_message": agent_said,
        "visitor_reply": visitor_said,
    }
    try:
        answers = await decider.decide(state, QUESTIONS)
    except DeciderUnavailable as error:
        log.warning("commit check unavailable", extra={"error": str(error)})
        return None
    read_back, said_yes = answers["read_back"], answers["said_yes"]
    log.info(
        "commit check",
        extra={"read_back": read_back.p_yes, "said_yes": said_yes.p_yes, "decider": decider.name},
    )
    if read_back.likely(THRESHOLD) and said_yes.likely(THRESHOLD):
        return Confirmed(value)
    return None


def ask_for_yes(read_back: str, tool: str) -> ToolResult:
    """What a writing tool returns until the visitor has said yes to its read-back."""
    return ToolResult(
        ok=False,
        data={
            "read_back": read_back,
            "next": f"Read this back and ask for a clear yes. Only after the yes, call {tool} "
            "again with the same details.",
        },
        choices=["Yes", "Change"],  # a tapped Yes is checked like a typed one (D99)
    )
