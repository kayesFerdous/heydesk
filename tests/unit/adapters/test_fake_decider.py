import pytest

from heydesk.adapters.decider.fake import FakeDecider
from heydesk.ports.decider import Choice, DeciderUnavailable, Score, YesNo

QUESTIONS = {
    "wants_human": YesNo("Is the customer asking to speak to a person?"),
    "topic": Choice("Pick the topic.", {"menu": "the menu", "none": "anything else"}),
    "sentiment": Score("How happy is the customer?"),
}


async def test_scripted_answers_then_calm_defaults() -> None:
    decider = FakeDecider([{"wants_human": 0.9}, DeciderUnavailable("down")])

    first = await decider.decide({"customer_latest_message": "a person please"}, QUESTIONS)
    assert first["wants_human"].likely(0.7) and first["wants_human"].p_yes == 0.9
    assert first["topic"].value == "none" and first["sentiment"].value == 1

    with pytest.raises(DeciderUnavailable):
        await decider.decide({}, QUESTIONS)
    assert not (await decider.decide({}, QUESTIONS))["wants_human"].likely()
