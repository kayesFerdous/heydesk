"""The `Decider` port: fast typed judgments (Jev), design §7.

Our names, not a vendor's; the Jev adapter maps them. Jev's rules: small
focused state, questions worded positively, a `none` option on every Choice,
no counting or date maths.
"""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Choice:
    """Pick exactly one option: {option id: what it means}."""

    instructions: str
    criteria: dict[str, str]


@dataclass(frozen=True)
class YesNo:
    """Is this statement true? Answers with a probability, not a boolean."""

    instructions: str


@dataclass(frozen=True)
class Score:
    instructions: str
    low: int = 1
    high: int = 5


type Question = Choice | YesNo | Score


@dataclass(frozen=True)
class Answer:
    """`value` is the option id (Choice), P(yes) (YesNo) or the score (Score)."""

    value: str | float | int | None
    confidence: float = 0.0

    @property
    def p_yes(self) -> float:
        return float(self.value) if isinstance(self.value, int | float) else 0.0

    def likely(self, threshold: float = 0.5) -> bool:
        """For a YesNo. Thresholds are tuned per gate from logged answers."""
        return self.p_yes >= threshold


class DeciderUnavailable(Exception):
    """Jev errored or timed out. The guard fails open; the commit check fails closed."""


class Decider(Protocol):
    name: str

    async def decide(
        self, state: dict[str, Any], questions: dict[str, Question]
    ) -> dict[str, Answer]: ...
