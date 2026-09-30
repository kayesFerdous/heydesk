"""FakeDecider: scripted answers. Deterministic, free and offline."""

from typing import Any

from heydesk.ports.decider import Answer, Choice, Question, Score, YesNo


class FakeDecider:
    """One dict of answers per expected call; a step can also be an exception to raise.

    Raw values: a float for a YesNo, an option id for a Choice, a
    (value, confidence) pair when the confidence matters. Anything unscripted
    gets a calm default: no, `none`, the lowest score.
    """

    name = "fake"

    def __init__(self, script: list[dict[str, Any] | Exception] | None = None) -> None:
        self.script = list(script or [])
        self.asked: list[dict[str, Any]] = []  # the state of every call

    async def decide(
        self, state: dict[str, Any], questions: dict[str, Question]
    ) -> dict[str, Answer]:
        self.asked.append(state)
        step = self.script.pop(0) if self.script else {}
        if isinstance(step, Exception):
            raise step
        return {k: _answer(step[k]) if k in step else _default(q) for k, q in questions.items()}


def _answer(raw: Any) -> Answer:
    if isinstance(raw, Answer):
        return raw
    if isinstance(raw, tuple):
        return Answer(raw[0], raw[1])
    if isinstance(raw, bool):
        return Answer(1.0 if raw else 0.0, 1.0)
    return Answer(raw, 1.0)


def _default(question: Question) -> Answer:
    match question:
        case YesNo():
            return Answer(0.0, 1.0)
        case Choice():
            return Answer("none", 1.0)
        case Score():
            return Answer(question.low, 1.0)
