"""The commit check (design §7): read back, then a yes, then save (rule 2).

A writing tool's first call returns the read-back, and `ask_for_yes` keeps it in the state.
The model judges the visitor's reply: only after a clear yes does it call the tool again.
Python checks that this call's details are the last ones read back on an earlier turn, so
the visitor heard exactly what is saved (D107).
"""

from typing import Any

from heydesk.domain.values import Confirmed
from heydesk.runtime.tool import ToolResult


def confirm[T](heard: str, value: T, read_back: str) -> Confirmed[T] | None:
    """`heard` is the read-back the visitor replied to (`ToolContext.heard`); `read_back` is
    this call's, as the visitor would hear it. Different, or none → nothing is saved."""
    return Confirmed(value) if heard and heard == read_back else None


def ask_for_yes(read_back: str, tool: str, card: dict[str, Any] | None = None) -> ToolResult:
    """What a writing tool returns until the visitor has said yes to its read-back. `card`:
    the same details as a summary card, for a screen (D110); the yes is still to `read_back`."""
    return ToolResult(
        ok=False,
        data={
            "read_back": read_back,
            "next": f"Read this back and ask for a clear yes. Only after their clear yes, call "
            f"{tool} again with the same details. A change, a question or a maybe is not a yes.",
        },
        state={"read_back": read_back},  # kept until another read-back replaces it
        choices=["Yes", "Change"],  # a tapped Yes is the visitor's words, like a typed one (D99)
        card=card,
    )
