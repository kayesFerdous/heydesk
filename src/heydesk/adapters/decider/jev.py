"""JevDecider: TypeSafe's Jev behind the `Decider` port (from Frontdesk's).

It translates and nothing more:

    YesNo  → Noul     P(yes); no confidence of its own, so how far it is from 0.5
    Choice → Choice   the option id; one we never offered → None
    Score  → Score    a level on a rubric "low".."high"; Jev counts levels from zero
"""

from typing import Any

from typesafe_sdk import AsyncTypeSafeClient, Noul, TypeSafeError
from typesafe_sdk import Choice as JevChoice
from typesafe_sdk import Question as JevQuestion
from typesafe_sdk import Score as JevScore

from heydesk.ports.decider import Answer, Choice, DeciderUnavailable, Question, Score, YesNo


class JevDecider:
    name = "jev"

    def __init__(self, client: AsyncTypeSafeClient, model: str) -> None:
        self.client = client
        self.model = model

    async def decide(
        self, state: dict[str, Any], questions: dict[str, Question]
    ) -> dict[str, Answer]:
        """Every question in one call."""
        asked = {key: _ask(q) for key, q in questions.items()}
        try:
            response = await self.client.system_one(state, asked, model=self.model)
        except (TypeSafeError, TimeoutError) as error:
            raise DeciderUnavailable(f"jev: {type(error).__name__}") from error
        return {key: _read(response.answers.get(key), q) for key, q in questions.items()}


def _ask(question: Question) -> JevQuestion:
    match question:
        case YesNo():
            return Noul(instructions=question.instructions)
        case Choice():
            return JevChoice(instructions=question.instructions, criteria=dict(question.criteria))
        case Score():
            levels = [str(n) for n in range(question.low, question.high + 1)]
            return JevScore(instructions=question.instructions, criteria=levels)


def _read(answer: Any, question: Question) -> Answer:
    if answer is None:  # left out: "they didn't say", not a guess
        return Answer(None, 0.0)
    match question:
        case YesNo():
            p = float(answer.noul)
            return Answer(p, abs(p - 0.5) * 2)
        case Choice():
            value = answer.choice if answer.choice in question.criteria else None
            return Answer(value, float(answer.confidence))
        case Score():
            return Answer(question.low + int(answer.score), float(answer.confidence))
